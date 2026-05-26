"""Pick the chemistry mention to substitute, applying the soft-preference cascade in spec §4.4."""
from __future__ import annotations

import random
from collections import Counter

from .positions import PositionRanges, offset_position


CORPUS_FREQ_FLOOR = 3


def _candidate_mentions_for_position(
    mentions: list[dict], ranges: PositionRanges, position: str
) -> list[dict]:
    out = []
    for m in mentions:
        pos = offset_position(m["start"], ranges)
        if pos == position:
            out.append(m)
    return out


def _has_parallel_mention(chebi_id: str, parallel_mentions: list[dict] | None) -> bool:
    if not parallel_mentions:
        return False
    return any(m["chebi_id"] == chebi_id for m in parallel_mentions)


def pick_mention(
    mentions: list[dict],
    ranges: PositionRanges,
    position: str,
    corpus_freq: Counter,
    *,
    require_parallel: bool = False,
    parallel_mentions: list[dict] | None = None,
    seed_salt: int = 0,
) -> tuple[dict | None, bool]:
    """
    Returns (chosen_mention, fallback_flag).

    Soft prefs (drop in this order if no candidate):
      1. corpus-wide frequency >= CORPUS_FREQ_FLOOR
      2. when require_parallel: chebi_id must also be a mention in parallel_mentions
    """
    rng = random.Random(seed_salt)
    in_position = _candidate_mentions_for_position(mentions, ranges, position)
    if not in_position:
        return None, False

    fallback = False

    def filter_pool(pool, freq_floor, parallel_required):
        out = []
        for m in pool:
            if corpus_freq.get(m["chebi_id"], 0) < freq_floor:
                continue
            if parallel_required and not _has_parallel_mention(m["chebi_id"], parallel_mentions):
                continue
            out.append(m)
        return out

    pool = filter_pool(in_position, CORPUS_FREQ_FLOOR, require_parallel)
    if not pool:
        # Drop corpus-frequency floor
        pool = filter_pool(in_position, 0, require_parallel)
        fallback = True
    if not pool and require_parallel:
        # Drop parallel-mention requirement
        pool = filter_pool(in_position, 0, False)
        fallback = True

    if not pool:
        return None, fallback
    rng.shuffle(pool)
    return pool[0], fallback
