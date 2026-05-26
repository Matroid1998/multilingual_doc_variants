"""Concept qualification + balanced sampling for Benchmark 1."""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass

import polars as pl

from ..config import (
    BENCH1_TARGET_COUNT,
    CHEBI_CONCEPTS_PARQUET,
    CORPUS_LINKED_PARQUET,
    LANGS,
    RNG_SEED,
)


# Coarse-type heuristic via ChEBI role/parent ids. Mapping is intentionally tiny:
# we just want a rough bucket to balance sampling. Anything else -> "other".
_ROLE_TO_BUCKET = {
    "CHEBI:33232": "drug",            # application
    "CHEBI:50906": "drug",            # role
    "CHEBI:35222": "drug",            # inhibitor
    "CHEBI:23888": "drug",            # drug
    "CHEBI:33893": "drug",            # reagent
    "CHEBI:60004": "salt",            # mixture
    "CHEBI:24432": "biochemical",     # biological role
    "CHEBI:33697": "biochemical",     # ribonucleic acid
    "CHEBI:33696": "biochemical",     # nucleic acid
    "CHEBI:36080": "biochemical",     # protein
    "CHEBI:60027": "polymer",         # polymer
    "CHEBI:50047": "small_molecule",  # organic molecular entity
    "CHEBI:25367": "small_molecule",  # molecule
}


def coarse_type(roles: list[str], formula: str | None) -> str:
    for r in roles or []:
        if r in _ROLE_TO_BUCKET:
            return _ROLE_TO_BUCKET[r]
    if formula and any(c.isdigit() for c in formula) and len(formula) < 30:
        return "small_molecule"
    return "other"


@dataclass
class ConceptCandidate:
    chebi_id: str
    star: int
    alias_lang_count: int
    mention_lang_count: int
    neighbor_count_in_corpus: int
    coarse: str


def _load_kg() -> pl.DataFrame:
    return pl.read_parquet(CHEBI_CONCEPTS_PARQUET)


def _load_links() -> pl.DataFrame:
    return pl.read_parquet(CORPUS_LINKED_PARQUET)


def _concepts_mentioned_with_lang(links_df: pl.DataFrame) -> dict[str, set[str]]:
    """chebi_id -> set of languages it appears in."""
    out: dict[str, set[str]] = defaultdict(set)
    for row in links_df.iter_rows(named=True):
        lang = row["language"]
        for m in (row["mentions"] or []):
            out[m["chebi_id"]].add(lang)
    return out


def qualifying_concepts(
    kg_df: pl.DataFrame | None = None,
    links_df: pl.DataFrame | None = None,
) -> tuple[list[ConceptCandidate], dict[str, set[str]]]:
    """
    Returns (candidates, mention_langs). Candidates pass all hard filters:
      - mentioned in >=2 distinct languages
      - aliases_combined non-empty in >=3 of 5 langs
      - >=1 neighbor concept also present in corpus
      - chebi_star == 3 preferred (otherwise allow 2; never 1 or unknown)
    """
    if kg_df is None:
        kg_df = _load_kg()
    if links_df is None:
        links_df = _load_links()
    mention_langs = _concepts_mentioned_with_lang(links_df)
    corpus_ids = set(mention_langs.keys())

    candidates: list[ConceptCandidate] = []
    for row in kg_df.iter_rows(named=True):
        cid = row["chebi_id"]
        if cid not in corpus_ids:
            continue
        if len(mention_langs[cid]) < 2:
            continue
        ac = row["aliases_combined"] or {}
        n_langs = sum(1 for lg in LANGS if ac.get(lg))
        if n_langs < 3:
            continue
        neigh_ids: set[str] = set()
        for field in (
            "siblings_isa", "siblings_role", "conjugate_pair",
            "tautomers", "stereo_or_tautomer_inchikey_block", "parent_hydride_family",
        ):
            for n in row[field] or []:
                neigh_ids.add(n)
        neigh_in_corpus = neigh_ids & corpus_ids
        if not neigh_in_corpus:
            continue
        star = row.get("chebi_star") or 0
        if star not in (2, 3):
            continue
        candidates.append(
            ConceptCandidate(
                chebi_id=cid,
                star=star,
                alias_lang_count=n_langs,
                mention_lang_count=len(mention_langs[cid]),
                neighbor_count_in_corpus=len(neigh_in_corpus),
                coarse=coarse_type(row.get("roles") or [], row.get("formula")),
            )
        )
    return candidates, mention_langs


def balanced_sample(
    candidates: list[ConceptCandidate],
    target: int = BENCH1_TARGET_COUNT,
    seed: int = RNG_SEED,
) -> list[ConceptCandidate]:
    """Stratified sample over (coarse type, alias_lang_count) with star=3 preference."""
    rng = random.Random(seed)
    star3 = [c for c in candidates if c.star == 3]
    star2 = [c for c in candidates if c.star == 2]

    def stratify(pool: list[ConceptCandidate], n: int) -> list[ConceptCandidate]:
        if not pool:
            return []
        if n >= len(pool):
            return list(pool)
        buckets: dict[tuple[str, int], list[ConceptCandidate]] = defaultdict(list)
        for c in pool:
            buckets[(c.coarse, c.alias_lang_count)].append(c)
        keys = list(buckets)
        rng.shuffle(keys)
        out: list[ConceptCandidate] = []
        # round-robin draw
        while len(out) < n and any(buckets[k] for k in keys):
            for k in keys:
                if not buckets[k]:
                    continue
                pick = rng.choice(buckets[k])
                buckets[k].remove(pick)
                out.append(pick)
                if len(out) == n:
                    break
        return out

    # Prefer star=3 first; fill remainder from star=2 only if short
    chosen = stratify(star3, target)
    if len(chosen) < target:
        chosen.extend(stratify(star2, target - len(chosen)))
    return chosen[:target]
