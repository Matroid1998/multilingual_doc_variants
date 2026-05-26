"""Stage 2 entry point: produce data/corpus/documents_linked.parquet."""
from __future__ import annotations

from collections import Counter

import polars as pl
from tqdm import tqdm

from ..config import CHEBI_CONCEPTS_PARQUET, CORPUS_LINKED_PARQUET, LANGS
from ..corpus import load_rows
from .dictionary import build_dictionaries
from .link import link_document


def _load_aliases_combined() -> dict[str, dict[str, list[str]]]:
    df = pl.read_parquet(CHEBI_CONCEPTS_PARQUET, columns=["chebi_id", "aliases_combined"])
    out: dict[str, dict[str, list[str]]] = {}
    for row in df.iter_rows(named=True):
        ac = row["aliases_combined"] or {}
        # polars may give us a dict already; ensure all langs are keys
        normed = {lg: list(ac.get(lg, []) or []) for lg in LANGS}
        out[row["chebi_id"]] = normed
    return out


def build() -> int:
    print("[stage2] loading KG aliases")
    aliases_combined = _load_aliases_combined()
    dicts = build_dictionaries(aliases_combined)
    print(f"[stage2] dictionary sizes: " + ", ".join(f"{lg}={len(dicts[lg].inverted)}" for lg in LANGS))

    rows = load_rows()
    out_rows = []
    mention_count_total = 0
    rows_with_mentions = 0
    for r in tqdm(rows, desc="[stage2] linking"):
        mentions = link_document(r.text, r.language, dicts, aliases_combined)
        primary = None
        if mentions:
            counter = Counter(m["chebi_id"] for m in mentions)
            primary = counter.most_common(1)[0][0]
            rows_with_mentions += 1
            mention_count_total += len(mentions)
        out_rows.append(
            {
                "id": r.id,
                "publication_number": r.publication_number,
                "language": r.language,
                "text": r.text,
                "mentions": mentions,
                "primary_chebi_id": primary,
            }
        )
    df = pl.DataFrame(out_rows)
    CORPUS_LINKED_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(CORPUS_LINKED_PARQUET)
    print(
        f"[stage2] wrote {CORPUS_LINKED_PARQUET} "
        f"({len(rows)} rows; {rows_with_mentions} with >=1 mention; {mention_count_total} mentions total)"
    )
    return len(rows)
