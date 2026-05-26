"""Stage 4 entry point: produce data/idea2/instances.jsonl + llm_cache.jsonl.

For each sampled source row, emit up to 9 variant records: A, B x 3 positions,
C x 3 positions, D, E. Records that fail rejection-style verification checks are not
written; rejections are tallied in the run summary.
"""
from __future__ import annotations

import json
import random
from collections import Counter

import polars as pl
from tqdm import tqdm

from ..config import (
    BENCH2_TARGET_COUNT,
    CHEBI_CONCEPTS_PARQUET,
    CORPUS_LINKED_PARQUET,
    IDEA2_INSTANCES_JSONL,
    IDEA2_RUN_SUMMARY,
    LANGS,
    OPENAI_MODEL,
    RNG_SEED,
)
from ..corpus import load_rows
from ..io_utils import append_jsonl
from .llm import LLMClient
from .mention_picker import pick_mention
from .positions import compute_position_ranges
from .select_rows import qualifying_source_rows, sample_source_rows
from .variants.b_in_set import pick_swap_lang_in_set, resolve_swap_term as b_swap_term
from .variants.c_out_of_set import pick_swap_lang_out_of_set, resolve_swap_term as c_swap_term
from .variants.d_noisy import perturb
from .variants.e_control import pick_noun_outside_mentions, translate_noun
from .verify import round_trip, semantic_preserved, source_term_absent_at_swap, term_presence_offset


POSITIONS = ("title", "first_sentence", "body")

# Refuse swap terms that are too short or look like common single-letter abbreviations —
# they collide with high-frequency vocabulary (e.g., French "or" = gold but also a conjunction).
MIN_SWAP_TERM_LEN = 3


def _is_usable_swap(term: str | None) -> bool:
    if not term:
        return False
    s = term.strip()
    if len(s) < MIN_SWAP_TERM_LEN:
        return False
    return True


# --- helpers --------------------------------------------------------------


def _kg_lookup(kg_df: pl.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in kg_df.iter_rows(named=True):
        out[row["chebi_id"]] = row
    return out


def _link_lookup(links_df: pl.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in links_df.iter_rows(named=True):
        out[row["id"]] = row
    return out


def _corpus_freq(links_df: pl.DataFrame) -> Counter:
    out: Counter = Counter()
    for row in links_df.iter_rows(named=True):
        for m in (row["mentions"] or []):
            out[m["chebi_id"]] += 1
    return out


def _parallel_row(
    publication_number: str, lang: str, links_by_pub: dict[str, list[dict]]
) -> dict | None:
    for r in links_by_pub.get(publication_number, []):
        if r["language"] == lang:
            return r
    return None


def _index_links_by_pub(links_df: pl.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in links_df.iter_rows(named=True):
        out.setdefault(row["publication_number"], []).append(row)
    return out


# --- LLM grammar-cleanup prompt ------------------------------------------
# We perform the substitution deterministically (single occurrence at the chosen
# offset), then ask the model only to adjust grammar around the inserted term.
# This avoids the LLM accidentally replacing other occurrences or breaking word
# boundaries.


def _deterministic_substitute(text: str, start: int, end: int, swap_term: str) -> tuple[str, int]:
    """Replace text[start:end] with swap_term. Returns (new_text, new_swap_offset)."""
    return text[:start] + swap_term + text[end:], start


def _cleanup_prompt(text: str, original: str, swap: str, swap_offset: int, src_lang: str, swap_lang: str) -> tuple[str, str]:
    # Give the model a short window around the substitution to focus its edits
    win = 200
    lo = max(0, swap_offset - win)
    hi = min(len(text), swap_offset + len(swap) + win)
    context_window = text[lo:hi]
    system = (
        f"The passage below is in {src_lang}. A single chemistry term has just been substituted with a "
        f"{swap_lang}-language term: '{swap}' (at character offset {swap_offset}, replacing the original "
        f"'{original}'). The rest of the passage is intact. "
        "Your task: return the passage with ONLY the minimum grammatical adjustments around the inserted "
        f"'{swap}' (article agreement, gender/number/case agreement, declension, light reordering) so the "
        "sentence reads well. Keep the inserted term EXACTLY as given (do not translate it back, do not "
        "change spelling or capitalization). Do not change anything outside the immediate sentence "
        "containing the inserted term. Do not remove or add content. Return ONLY the full passage, "
        "no commentary, no markdown, no quoting."
    )
    user = (
        f"INSERTED TERM (must remain exactly): {swap}\n"
        f"ORIGINAL TERM (now removed): {original}\n"
        f"SENTENCE WINDOW for reference: ...{context_window}...\n\n"
        f"FULL PASSAGE (already substituted; return with grammar fixed):\n{text}\n"
    )
    return system, user


# --- per-variant builders ------------------------------------------------


def _emit_a_clean(src) -> dict:
    return {
        "instance_id": f"{src.id}__A_clean__na",
        "source_row_id": src.id,
        "publication_number": src.publication_number,
        "available_languages": sorted(src.l_avail),
        "source_language": src.language,
        "variant_tag": "A_clean",
        "position": "na",
        "text": src.text,
        "swap_lang": None,
        "original_term": None,
        "swap_term": None,
        "swap_char_offset": None,
        "chebi_id": None,
        "control_style": None,
        "round_trip_flag": None,
        "fallback_flag": False,
    }


def _do_llm_rewrite_and_verify(
    client: LLMClient,
    src,
    swap_lang: str,
    original_term: str,
    swap_term: str,
    original_start: int,
    original_end: int,
    rejections: Counter,
) -> tuple[str, int, bool] | None:
    """Returns (rewrite_text, swap_offset, round_trip_flag) or None on rejection.

    Substitution is fully deterministic: text[original_start:original_end] -> swap_term.
    This avoids the LLM's tendency to also substitute other occurrences of the source term.
    Verification (semantic preservation, round-trip) still uses LLM calls.
    """
    rewrite, deterministic_offset = _deterministic_substitute(
        src.text, original_start, original_end, swap_term
    )
    # Term-presence check still matters: catches cases where swap_term coincidentally
    # appears elsewhere in the doc, which would break downstream "find the swap" logic.
    swap_offset = term_presence_offset(rewrite, swap_term)
    if swap_offset is None:
        rejections["term_presence"] += 1
        return None
    # source-term-absence at the swap site is true by construction of the deterministic
    # substitution; skip that check here.
    if not semantic_preserved(client, src.text, rewrite, original_term, swap_term):
        rejections["semantic_preservation"] += 1
        return None
    rt_ok = round_trip(client, rewrite, swap_term, swap_lang, src.language, original_term)
    return rewrite, swap_offset, (not rt_ok)


def _emit_b(
    src,
    position: str,
    field_texts: dict[str, str],
    kg: dict[str, dict],
    links_by_pub: dict[str, list[dict]],
    corpus_freq: Counter,
    client: LLMClient,
    seed: int,
    rejections: Counter,
) -> dict | None:
    swap_lang = pick_swap_lang_in_set(src.l_avail, src.language, seed=seed + hash(("B", src.id, position)) % (1 << 30))
    if swap_lang is None:
        return None
    parallel = _parallel_row(src.publication_number, swap_lang, links_by_pub)
    parallel_mentions = parallel["mentions"] if parallel else None

    ranges = compute_position_ranges(
        field_texts["title"], field_texts["abstract"], field_texts["description"],
        field_texts["first_claim"], field_texts["context"], src.language,
    )
    mention, fallback = pick_mention(
        src.mentions, ranges, position, corpus_freq,
        require_parallel=True, parallel_mentions=parallel_mentions,
        seed_salt=seed + hash(("B", src.id, position)) % (1 << 30),
    )
    if mention is None:
        return None
    cid = mention["chebi_id"]
    kg_row = kg.get(cid)
    swap_term, kg_fallback = b_swap_term(cid, swap_lang, parallel_mentions, kg_row)
    fallback = fallback or kg_fallback
    if not _is_usable_swap(swap_term):
        return None

    result = _do_llm_rewrite_and_verify(
        client, src, swap_lang, mention["surface"], swap_term, mention["start"], mention["end"], rejections
    )
    if result is None:
        return None
    rewrite, swap_offset, rt_flag = result
    return {
        "instance_id": f"{src.id}__B_in_set__{position}",
        "source_row_id": src.id,
        "publication_number": src.publication_number,
        "available_languages": sorted(src.l_avail),
        "source_language": src.language,
        "variant_tag": "B_in_set",
        "position": position,
        "text": rewrite,
        "swap_lang": swap_lang,
        "original_term": mention["surface"],
        "swap_term": swap_term,
        "swap_char_offset": swap_offset,
        "chebi_id": cid,
        "control_style": None,
        "round_trip_flag": rt_flag,
        "fallback_flag": fallback,
    }


def _emit_c(
    src,
    position: str,
    field_texts: dict[str, str],
    kg: dict[str, dict],
    corpus_freq: Counter,
    client: LLMClient,
    seed: int,
    rejections: Counter,
) -> dict | None:
    swap_lang = pick_swap_lang_out_of_set(src.l_avail, seed=seed + hash(("C", src.id, position)) % (1 << 30))
    if swap_lang is None:
        return None
    ranges = compute_position_ranges(
        field_texts["title"], field_texts["abstract"], field_texts["description"],
        field_texts["first_claim"], field_texts["context"], src.language,
    )
    mention, fallback = pick_mention(
        src.mentions, ranges, position, corpus_freq,
        require_parallel=False, parallel_mentions=None,
        seed_salt=seed + hash(("C", src.id, position)) % (1 << 30),
    )
    if mention is None:
        return None
    cid = mention["chebi_id"]
    swap_term = c_swap_term(cid, swap_lang, kg.get(cid))
    if not _is_usable_swap(swap_term):
        return None

    result = _do_llm_rewrite_and_verify(
        client, src, swap_lang, mention["surface"], swap_term, mention["start"], mention["end"], rejections
    )
    if result is None:
        return None
    rewrite, swap_offset, rt_flag = result
    return {
        "instance_id": f"{src.id}__C_out_of_set__{position}",
        "source_row_id": src.id,
        "publication_number": src.publication_number,
        "available_languages": sorted(src.l_avail),
        "source_language": src.language,
        "variant_tag": "C_out_of_set",
        "position": position,
        "text": rewrite,
        "swap_lang": swap_lang,
        "original_term": mention["surface"],
        "swap_term": swap_term,
        "swap_char_offset": swap_offset,
        "chebi_id": cid,
        "control_style": None,
        "round_trip_flag": rt_flag,
        "fallback_flag": fallback,
    }


def _emit_d(src, field_texts: dict[str, str], corpus_freq: Counter, seed: int) -> dict | None:
    ranges = compute_position_ranges(
        field_texts["title"], field_texts["abstract"], field_texts["description"],
        field_texts["first_claim"], field_texts["context"], src.language,
    )
    mention, fallback = pick_mention(
        src.mentions, ranges, "body", corpus_freq,
        require_parallel=False, parallel_mentions=None,
        seed_salt=seed + hash(("D", src.id)) % (1 << 30),
    )
    if mention is None:
        return None
    original = mention["surface"]
    swap_term = perturb(original, seed=seed + hash(("Dperm", src.id)) % (1 << 30))
    if swap_term == original or not swap_term:
        return None
    # In-place replacement of THIS occurrence
    start, end = mention["start"], mention["end"]
    new_text = src.text[:start] + swap_term + src.text[end:]
    swap_offset = start
    return {
        "instance_id": f"{src.id}__D_noisy__body",
        "source_row_id": src.id,
        "publication_number": src.publication_number,
        "available_languages": sorted(src.l_avail),
        "source_language": src.language,
        "variant_tag": "D_noisy",
        "position": "body",
        "text": new_text,
        "swap_lang": src.language,
        "original_term": original,
        "swap_term": swap_term,
        "swap_char_offset": swap_offset,
        "chebi_id": mention["chebi_id"],
        "control_style": None,
        "round_trip_flag": None,
        "fallback_flag": fallback,
    }


def _emit_e(
    src,
    field_texts: dict[str, str],
    client: LLMClient,
    seed: int,
    rejections: Counter,
) -> dict | None:
    ranges = compute_position_ranges(
        field_texts["title"], field_texts["abstract"], field_texts["description"],
        field_texts["first_claim"], field_texts["context"], src.language,
    )
    mention_spans = [(m["start"], m["end"]) for m in src.mentions]
    pick = pick_noun_outside_mentions(
        src.text, mention_spans, ranges.body, seed=seed + hash(("E", src.id)) % (1 << 30)
    )
    if pick is None:
        return None
    noun, noun_start, noun_end = pick

    # Decide style (in-set vs out-of-set) and target language
    rng = random.Random(seed + hash(("Esty", src.id)) % (1 << 30))
    in_set_pool = sorted(src.l_avail - {src.language})
    out_set_pool = sorted(set(LANGS) - src.l_avail)
    styles = []
    if in_set_pool:
        styles.append("in_set")
    if out_set_pool:
        styles.append("out_of_set")
    if not styles:
        return None
    style = rng.choice(styles)
    swap_lang = rng.choice(in_set_pool if style == "in_set" else out_set_pool)

    swap_term = translate_noun(client, noun, src.language, swap_lang)
    if not swap_term or swap_term.casefold() == noun.casefold():
        rejections["e_no_translation"] += 1
        return None

    # Fluent LLM substitution (same as B/C). Verification reuses presence/absence/semantic.
    result = _do_llm_rewrite_and_verify(
        client, src, swap_lang, noun, swap_term, noun_start, noun_end, rejections
    )
    if result is None:
        return None
    rewrite, swap_offset, rt_flag = result
    return {
        "instance_id": f"{src.id}__E_control_nonchem__body",
        "source_row_id": src.id,
        "publication_number": src.publication_number,
        "available_languages": sorted(src.l_avail),
        "source_language": src.language,
        "variant_tag": "E_control_nonchem",
        "position": "body",
        "text": rewrite,
        "swap_lang": swap_lang,
        "original_term": noun,
        "swap_term": swap_term,
        "swap_char_offset": swap_offset,
        "chebi_id": None,
        "control_style": style,
        "round_trip_flag": rt_flag,
        "fallback_flag": False,
    }


# --- main ----------------------------------------------------------------


def build(target: int = BENCH2_TARGET_COUNT, seed: int = RNG_SEED, limit: int | None = None) -> int:
    kg_df = pl.read_parquet(CHEBI_CONCEPTS_PARQUET)
    links_df = pl.read_parquet(CORPUS_LINKED_PARQUET)
    kg = _kg_lookup(kg_df)
    links_by_pub = _index_links_by_pub(links_df)
    corpus_freq = _corpus_freq(links_df)

    candidates = qualifying_source_rows(links_df)
    print(f"[stage4] {len(candidates)} source-row candidates")
    chosen = sample_source_rows(candidates, target=target, seed=seed)
    if limit is not None:
        chosen = chosen[:limit]
    print(f"[stage4] sampled {len(chosen)} rows (target={target}, limit={limit})")

    # Need per-field text for position computation; load via corpus.py
    rows_by_id = {r.id: r for r in load_rows()}

    client = LLMClient()
    print(f"[stage4] LLM model: {client.model}")

    rejections: Counter = Counter()
    written = 0
    skips: Counter = Counter()

    # Truncate output file so reruns are clean
    IDEA2_INSTANCES_JSONL.parent.mkdir(parents=True, exist_ok=True)
    IDEA2_INSTANCES_JSONL.write_text("")

    for src in tqdm(chosen, desc="[stage4] generating variants"):
        cr = rows_by_id.get(src.id)
        if cr is None:
            continue
        field_texts = {
            "title": cr.title, "abstract": cr.abstract, "description": cr.description,
            "first_claim": cr.first_claim, "context": cr.context,
        }

        # A — always
        append_jsonl(IDEA2_INSTANCES_JSONL, _emit_a_clean(src))
        written += 1

        # B and C at each position
        b_eligible = bool(src.l_avail - {src.language})
        c_eligible = bool(set(LANGS) - src.l_avail)

        for pos in POSITIONS:
            if b_eligible:
                rec = _emit_b(src, pos, field_texts, kg, links_by_pub, corpus_freq, client, seed, rejections)
                if rec is None:
                    skips[f"B/{pos}"] += 1
                else:
                    append_jsonl(IDEA2_INSTANCES_JSONL, rec)
                    written += 1
            if c_eligible:
                rec = _emit_c(src, pos, field_texts, kg, corpus_freq, client, seed, rejections)
                if rec is None:
                    skips[f"C/{pos}"] += 1
                else:
                    append_jsonl(IDEA2_INSTANCES_JSONL, rec)
                    written += 1

        # D
        rec = _emit_d(src, field_texts, corpus_freq, seed)
        if rec is None:
            skips["D/body"] += 1
        else:
            append_jsonl(IDEA2_INSTANCES_JSONL, rec)
            written += 1

        # E
        rec = _emit_e(src, field_texts, client, seed, rejections)
        if rec is None:
            skips["E/body"] += 1
        else:
            append_jsonl(IDEA2_INSTANCES_JSONL, rec)
            written += 1

    summary = {
        "source_rows": len(chosen),
        "records_written": written,
        "skips": dict(skips),
        "rejections": dict(rejections),
        "model": client.model,
        "seed": seed,
    }
    IDEA2_RUN_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    IDEA2_RUN_SUMMARY.write_text(json.dumps(summary, indent=2))
    print(f"[stage4] wrote {written} records to {IDEA2_INSTANCES_JSONL}")
    print(f"[stage4] run summary -> {IDEA2_RUN_SUMMARY}")
    return written
