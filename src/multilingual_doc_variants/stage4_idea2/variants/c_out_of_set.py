"""Variant C: out-of-set code-switch. Helper — swap-language picker only."""
from __future__ import annotations

import random

from ...config import LANGS


def pick_swap_lang_out_of_set(l_avail: set[str], seed: int) -> str | None:
    pool = sorted(set(LANGS) - l_avail)
    if not pool:
        return None
    return random.Random(seed).choice(pool)
