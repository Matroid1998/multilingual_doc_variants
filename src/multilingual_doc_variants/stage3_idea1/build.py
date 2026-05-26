"""Stage 3 entry point: produce data/idea1/instances.jsonl."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import polars as pl

from ..config import (
    BENCH1_TARGET_COUNT,
    CHEBI_CONCEPTS_PARQUET,
    CORPUS_LINKED_PARQUET,
    IDEA1_INSTANCES_JSONL,
    IDEA1_RUN_SUMMARY,
    LANGS,
    RNG_SEED,
)
from ..io_utils import write_jsonl
from .hard_negatives import NEIGHBOR_FIELDS, hard_negatives, index_corpus_by_chebi, pick_gold_document
from .select import balanced_sample, qualifying_concepts


def _kg_lookup(kg_df: pl.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in kg_df.iter_rows(named=True):
        out[row["chebi_id"]] = row
    return out


def _neighbor_map_for(row: dict) -> dict[str, tuple[str, ...]]:
    """For a KG row: {neighbor_chebi_id: (relation, ...)}"""
    out: dict[str, list[str]] = defaultdict(list)
    for field in NEIGHBOR_FIELDS:
        for n in row.get(field) or []:
            out[n].append(field)
    return {k: tuple(v) for k, v in out.items()}


def build(target: int = BENCH1_TARGET_COUNT, seed: int = RNG_SEED) -> int:
    kg_df = pl.read_parquet(CHEBI_CONCEPTS_PARQUET)
    links_df = pl.read_parquet(CORPUS_LINKED_PARQUET)
    kg = _kg_lookup(kg_df)

    candidates, _ = qualifying_concepts(kg_df, links_df)
    print(f"[stage3] {len(candidates)} concepts pass hard filters")
    chosen = balanced_sample(candidates, target=target, seed=seed)
    print(f"[stage3] sampled {len(chosen)} concepts (target={target})")

    links_rows = list(links_df.iter_rows(named=True))
    by_chebi = index_corpus_by_chebi(links_rows)

    instances: list[dict] = []
    relation_counts: Counter = Counter()
    neg_lang_counts: Counter = Counter()
    gold_lang_counts: Counter = Counter()
    skipped_no_negs = 0
    for cand in chosen:
        kg_row = kg[cand.chebi_id]
        gold_doc, gold_language = pick_gold_document(cand.chebi_id, by_chebi, seed=seed)
        if gold_doc is None:
            continue
        gold_lang_counts[gold_language] += 1
        neighbor_map = _neighbor_map_for(kg_row)
        neg_docs, used_neighbor_ids = hard_negatives(
            cand.chebi_id,
            neighbor_map,
            by_chebi,
            seed=seed,
            gold_language=gold_language,
        )
        if not neg_docs:
            skipped_no_negs += 1
            continue
        for d in neg_docs:
            relation_counts[d["relation"]] += 1
            neg_lang_counts[d["language"]] += 1

        neighbor_concepts = []
        for n_cid in used_neighbor_ids:
            n_row = kg.get(n_cid)
            if n_row is None:
                continue
            neighbor_concepts.append(
                {
                    "chebi_id": n_cid,
                    "chebi_name_en": n_row.get("chebi_name_en"),
                    "aliases_combined": n_row.get("aliases_combined") or {},
                    "relation": neighbor_map.get(n_cid, ("unknown",))[0],
                }
            )

        instances.append(
            {
                "chebi_id": cand.chebi_id,
                "inchikey": kg_row.get("inchikey"),
                "multilingual_aliases": kg_row.get("aliases_combined") or {lg: [] for lg in LANGS},
                "gold_language": gold_language,
                "gold_document": gold_doc,
                "hard_negative_documents": neg_docs,
                "neighbor_concepts": neighbor_concepts,
            }
        )

    n = write_jsonl(IDEA1_INSTANCES_JSONL, instances)
    summary = {
        "instances_written": n,
        "qualifying_concepts": len(candidates),
        "sample_target": target,
        "skipped_no_cross_lingual_negs": skipped_no_negs,
        "relation_counts": dict(relation_counts),
        "gold_doc_languages": dict(gold_lang_counts),
        "negative_doc_languages": dict(neg_lang_counts),
        "seed": seed,
    }
    IDEA1_RUN_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    IDEA1_RUN_SUMMARY.write_text(json.dumps(summary, indent=2))
    print(f"[stage3] wrote {n} instances to {IDEA1_INSTANCES_JSONL}")
    print(f"[stage3] run summary -> {IDEA1_RUN_SUMMARY}")
    return n
