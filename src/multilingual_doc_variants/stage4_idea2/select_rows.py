"""Source row qualification + sampling for Benchmark 2."""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

import polars as pl

from ..config import (
    BENCH2_TARGET_COUNT,
    CORPUS_LINKED_PARQUET,
    MIN_TOKENS_FOR_SOURCE_ROW,
    RNG_SEED,
)
from ..corpus import compute_l_avail, load_rows


@dataclass
class SourceCandidate:
    id: str
    publication_number: str
    language: str
    text: str
    mentions: list[dict]
    l_avail: set[str]


def _token_count(text: str, lang: str) -> int:
    if lang == "zh":
        return int(len(text) / 1.5)
    return len(text.split())


def qualifying_source_rows(links_df: pl.DataFrame | None = None) -> list[SourceCandidate]:
    if links_df is None:
        links_df = pl.read_parquet(CORPUS_LINKED_PARQUET)
    rows = load_rows()
    l_avail = compute_l_avail(rows)

    candidates: list[SourceCandidate] = []
    for row in links_df.iter_rows(named=True):
        mentions = row["mentions"] or []
        if not mentions:
            continue
        if _token_count(row["text"], row["language"]) < MIN_TOKENS_FOR_SOURCE_ROW:
            continue
        candidates.append(
            SourceCandidate(
                id=row["id"],
                publication_number=row["publication_number"],
                language=row["language"],
                text=row["text"],
                mentions=list(mentions),
                l_avail=l_avail[row["publication_number"]],
            )
        )
    return candidates


def sample_source_rows(
    candidates: list[SourceCandidate],
    target: int = BENCH2_TARGET_COUNT,
    seed: int = RNG_SEED,
) -> list[SourceCandidate]:
    rng = random.Random(seed)

    by_pub: dict[str, list[SourceCandidate]] = defaultdict(list)
    for c in candidates:
        by_pub[c.publication_number].append(c)

    # One row per publication_number; soft preference for |L_avail| in {2, 3}
    pub_keys = list(by_pub)
    rng.shuffle(pub_keys)
    pub_keys.sort(key=lambda p: 0 if len(by_pub[p][0].l_avail) in (2, 3) else 1)

    chosen: list[SourceCandidate] = []
    for p in pub_keys:
        chosen.append(rng.choice(by_pub[p]))
        if len(chosen) == target:
            break
    return chosen
