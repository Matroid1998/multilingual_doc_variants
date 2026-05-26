"""Variant E: non-chemistry control. Picks a noun outside any chemistry mention span."""
from __future__ import annotations

import random
from functools import lru_cache

from ...config import LANGS

_SPACY_MODEL = "xx_ent_wiki_sm"  # multilingual; pos tagging is heuristic but adequate


@lru_cache(maxsize=1)
def _load_nlp():
    import spacy
    try:
        return spacy.load(_SPACY_MODEL)
    except Exception:
        # Last-resort: blank pipeline with sentence splitter only — POS will be empty
        return spacy.blank("xx")


def pick_noun_outside_mentions(
    text: str,
    mention_spans: list[tuple[int, int]],
    body_ranges: list[tuple[int, int]],
    seed: int,
) -> tuple[str, int, int] | None:
    """Returns (token_text, start, end) for a noun-ish token within body, outside mentions."""
    nlp = _load_nlp()
    doc = nlp(text)
    rng = random.Random(seed)

    def in_body(start: int, end: int) -> bool:
        return any(bs <= start and end <= be for bs, be in body_ranges)

    def overlaps_mention(start: int, end: int) -> bool:
        return any(not (end <= ms or start >= me) for ms, me in mention_spans)

    candidates: list[tuple[str, int, int]] = []
    for tok in doc:
        if not tok.text.strip():
            continue
        # Heuristic: POS == NOUN where available, else any alphabetic >= 4 chars
        pos_ok = (getattr(tok, "pos_", "") == "NOUN") or (
            tok.is_alpha and len(tok.text) >= 4 and not getattr(tok, "pos_", "")
        )
        if not pos_ok:
            continue
        start, end = tok.idx, tok.idx + len(tok.text)
        if not in_body(start, end):
            continue
        if overlaps_mention(start, end):
            continue
        candidates.append((tok.text, start, end))
    if not candidates:
        return None
    return rng.choice(candidates)


# Noun translation is now handled by LLMClient.generate_swap_term in build.py.
# No fixed-vocab table; the LLM is given the full document for disambiguation.
