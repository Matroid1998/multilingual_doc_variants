"""Mine confusable-neighbor hard-negative documents per gold concept."""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Iterable

from ..config import HARD_NEG_PER_GOLD


NEIGHBOR_FIELDS = (
    "siblings_isa",
    "siblings_role",
    "conjugate_pair",
    "tautomers",
    "stereo_or_tautomer_inchikey_block",
    "parent_hydride_family",
)


def index_corpus_by_chebi(links_rows: Iterable[dict]) -> dict[str, list[dict]]:
    """chebi_id -> list of {row_dict, mentions_for_this_cid} for every doc containing it."""
    out: dict[str, list[dict]] = defaultdict(list)
    for row in links_rows:
        for m in (row["mentions"] or []):
            cid = m["chebi_id"]
            out[cid].append(row)
    # Dedup rows per cid (a row may mention cid multiple times)
    deduped: dict[str, list[dict]] = {}
    for cid, rows in out.items():
        seen_ids: set[str] = set()
        keep: list[dict] = []
        for r in rows:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            keep.append(r)
        deduped[cid] = keep
    return deduped


def _doc_payload(row: dict, cid: str) -> dict:
    offsets = [m for m in (row["mentions"] or []) if m["chebi_id"] == cid]
    return {
        "id": row["id"],
        "publication_number": row["publication_number"],
        "language": row["language"],
        "text": row["text"],
        "mentions": offsets,
    }


def pick_gold_document(
    cid: str, by_chebi: dict[str, list[dict]], seed: int
) -> tuple[dict | None, str | None]:
    """
    Pick exactly one gold document for `cid`.

    Language is uniformly sampled from the set of languages where `cid` is mentioned;
    within that language a doc is uniformly sampled. Returns (gold_doc, gold_language).
    """
    rows = by_chebi.get(cid) or []
    if not rows:
        return None, None
    rng = random.Random(seed + hash(("gold", cid)) % (1 << 31))
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_lang[r["language"]].append(r)
    lang = rng.choice(sorted(by_lang.keys()))
    candidates = by_lang[lang]
    rng.shuffle(candidates)
    return _doc_payload(candidates[0], cid), lang


def hard_negatives(
    gold_cid: str,
    neighbor_map: dict[str, tuple[str, ...]],
    by_chebi: dict[str, list[dict]],
    seed: int,
    *,
    gold_language: str,
    max_neg: int = HARD_NEG_PER_GOLD,
) -> tuple[list[dict], list[dict]]:
    """
    Returns (negative_documents, neighbor_concepts_used).

    `neighbor_map[neighbor_cid] = (relation_tag, ...)` — a neighbor may appear via multiple
    relations; the first one is used in the output.
    """
    rng = random.Random(seed + hash(gold_cid) % (1 << 31))

    # Collect candidate (neighbor_cid, doc, relation) triples. Two filters:
    #   1. doc must not mention gold_cid
    #   2. doc's language must differ from gold_language (cross-lingual neg by construction)
    pool: list[tuple[str, dict, str]] = []
    used_neighbor_ids: set[str] = set()
    for neighbor_cid, relations in neighbor_map.items():
        if neighbor_cid == gold_cid:
            continue
        for doc in by_chebi.get(neighbor_cid, []):
            if doc["language"] == gold_language:
                continue
            if any(m["chebi_id"] == gold_cid for m in (doc["mentions"] or [])):
                continue
            pool.append((neighbor_cid, doc, relations[0]))

    if not pool:
        return [], []

    rng.shuffle(pool)

    # Coverage-aware selection: diversify across neighbors and across languages.
    chosen: list[tuple[str, dict, str]] = []
    used_pairs: set[tuple[str, str]] = set()  # (neighbor_cid, doc_id)
    used_doc_ids: set[str] = set()

    def score(triple, current_chosen):
        n_cid, doc, _ = triple
        n_neighbors_seen = len({t[0] for t in current_chosen})
        n_langs_seen = len({t[1]["language"] for t in current_chosen})
        bonus = 0
        if n_cid not in {t[0] for t in current_chosen}:
            bonus += 2
        if doc["language"] not in {t[1]["language"] for t in current_chosen}:
            bonus += 1
        return bonus

    while pool and len(chosen) < max_neg:
        pool.sort(key=lambda t: score(t, chosen), reverse=True)
        pick = pool.pop(0)
        n_cid, doc, _ = pick
        pair = (n_cid, doc["id"])
        if pair in used_pairs or doc["id"] in used_doc_ids:
            continue
        chosen.append(pick)
        used_pairs.add(pair)
        used_doc_ids.add(doc["id"])
        used_neighbor_ids.add(n_cid)

    neg_docs: list[dict] = []
    for n_cid, doc, relation in chosen:
        offsets = [m for m in (doc["mentions"] or []) if m["chebi_id"] == n_cid]
        neg_docs.append(
            {
                "id": doc["id"],
                "publication_number": doc["publication_number"],
                "language": doc["language"],
                "text": doc["text"],
                "mentions": offsets,
                "neighbor_chebi_id": n_cid,
                "relation": relation,
            }
        )
    return neg_docs, sorted(used_neighbor_ids)
