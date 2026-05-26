"""Variant B: in-set code-switch (swap one chemistry term into another L_avail language)."""
from __future__ import annotations

import random

from ...config import LANGS


def pick_swap_lang_in_set(l_avail: set[str], source_lang: str, seed: int) -> str | None:
    pool = sorted(l_avail - {source_lang})
    if not pool:
        return None
    return random.Random(seed).choice(pool)


def resolve_swap_term(
    chebi_id: str,
    swap_lang: str,
    parallel_mentions: list[dict] | None,
    kg_row: dict | None,
) -> tuple[str | None, bool]:
    """
    Returns (term, used_kg_fallback).

    Preference order:
      1. parallel-row mention surface that matches aliases_chebi[swap_lang][0] (primary ChEBI name)
      2. any parallel-row surface for the same chebi_id
      3. wikipedia_titles[swap_lang]
      4. aliases_chebi[swap_lang][0]   (KG fallback)
    """
    if kg_row is None:
        return None, True
    aliases_chebi = (kg_row.get("aliases_chebi") or {}).get(swap_lang) or []
    wiki = (kg_row.get("wikipedia_titles") or {}).get(swap_lang)
    primary = aliases_chebi[0] if aliases_chebi else None

    if parallel_mentions:
        parallel_surfaces = [m["surface"] for m in parallel_mentions if m["chebi_id"] == chebi_id]
        if parallel_surfaces:
            if primary:
                for s in parallel_surfaces:
                    if s.casefold() == primary.casefold():
                        return s, False
            if wiki:
                for s in parallel_surfaces:
                    if s.casefold() == wiki.casefold():
                        return s, False
            return parallel_surfaces[0], False
    # Fallback to KG
    if aliases_chebi:
        return aliases_chebi[0], True
    if wiki:
        return wiki, True
    return None, True
