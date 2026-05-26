"""Variant C: out-of-set code-switch (swap into a language not in L_avail)."""
from __future__ import annotations

import random

from ...config import LANGS


def pick_swap_lang_out_of_set(l_avail: set[str], seed: int) -> str | None:
    pool = sorted(set(LANGS) - l_avail)
    if not pool:
        return None
    return random.Random(seed).choice(pool)


def resolve_swap_term(chebi_id: str, swap_lang: str, kg_row: dict | None) -> str | None:
    if kg_row is None:
        return None
    aliases = (kg_row.get("aliases_chebi") or {}).get(swap_lang) or []
    if aliases:
        return aliases[0]
    wiki = (kg_row.get("wikipedia_titles") or {}).get(swap_lang)
    return wiki
