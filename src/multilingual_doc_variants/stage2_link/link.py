"""Longest-match dictionary scan with ambiguity disambiguation."""
from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any

import regex as re

from ..io_utils import fold, nfc
from .dictionary import LangDict

# Word-class character (Unicode letters + digits + underscore). For CJK languages we don't
# enforce word boundaries; for Latin scripts we require the match's start and end to lie at
# word boundaries to avoid matching "alkohol" inside "alkoholhaltiger".
_WORD_CHAR = re.compile(r"[\p{L}\p{N}_]")


def _at_word_boundary(text: str, start: int, end: int) -> bool:
    before_ok = start == 0 or not _WORD_CHAR.match(text[start - 1])
    after_ok = end == len(text) or not _WORD_CHAR.match(text[end])
    return before_ok and after_ok


def _build_folded_with_map(text: str, lang: str) -> tuple[str, list[int]]:
    """
    Returns (folded_text, idx_map) where folded_text[i]'s character corresponds to
    text_nfc[idx_map[i]]. NFC normalization is applied first so character indexing
    is stable, then casefold (skipped for zh).

    Casefolding can change character count (e.g. 'ß' -> 'ss'), so we track a mapping
    from the folded string's index back to the NFC string's index. Offsets reported
    to the user are NFC-string offsets (which match the original `text` if it was
    already NFC, which it is for the corpus).
    """
    nfc_text = nfc(text)
    if lang == "zh":
        return nfc_text, list(range(len(nfc_text)))
    folded_parts: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(nfc_text):
        f = ch.casefold()
        folded_parts.append(f)
        idx_map.extend([i] * len(f))
    return "".join(folded_parts), idx_map


def _resolve_ambiguous(
    candidates: list[tuple[str, str]],
    other_mention_chebi_ids: Counter,
) -> tuple[str, str]:
    """Pick the (chebi_id, original_alias) whose chebi_id is most-mentioned elsewhere in doc."""
    if len(candidates) == 1:
        return candidates[0]
    best = candidates[0]
    best_score = other_mention_chebi_ids.get(best[0], 0)
    for cand in candidates[1:]:
        score = other_mention_chebi_ids.get(cand[0], 0)
        if score > best_score:
            best = cand
            best_score = score
    return best


def link_document(
    text: str,
    lang: str,
    dicts: dict[str, LangDict],
    aliases_combined: dict[str, dict[str, list[str]]] | None = None,
) -> list[dict[str, Any]]:
    """
    Returns a list of mention dicts: {surface, start, end, chebi_id}.
    Offsets are relative to NFC(text) (== text for the corpus, which is already NFC).
    """
    if lang not in dicts:
        return []
    ld = dicts[lang]
    if ld.automaton is None:
        return []
    folded, idx_map = _build_folded_with_map(text, lang)
    nfc_text = nfc(text)

    # 1) Collect all matches (end_idx, key) — Latin-script langs require word boundaries.
    raw: list[tuple[int, int, str]] = []  # (start_in_folded, end_in_folded_exclusive, key)
    for end_idx, key in ld.automaton.iter(folded):
        start = end_idx - len(key) + 1
        if lang != "zh":
            nfc_start = idx_map[start] if start < len(idx_map) else len(nfc_text)
            nfc_end = idx_map[end_idx] + 1 if end_idx < len(idx_map) else len(nfc_text)
            if not _at_word_boundary(nfc_text, nfc_start, nfc_end):
                continue
        raw.append((start, end_idx + 1, key))

    if not raw:
        return []

    # 2) For each start position keep only the longest match
    by_start: dict[int, tuple[int, int, str]] = {}
    for s, e, k in raw:
        cur = by_start.get(s)
        if cur is None or (e - s) > (cur[1] - cur[0]):
            by_start[s] = (s, e, k)

    # 3) Sweep left-to-right keeping non-overlapping longest runs.
    #    Greedy: at each step pick the match whose start is earliest; among ties prefer longer.
    starts = sorted(by_start.keys())
    selected: list[tuple[int, int, str]] = []
    cursor = -1
    for s in starts:
        seg = by_start[s]
        if seg[0] < cursor:
            continue
        # Try to find a longer match that begins in [s, current_end)
        end = seg[1]
        best = seg
        for s2 in starts:
            if s2 <= s:
                continue
            if s2 >= end:
                break
            cand = by_start[s2]
            if cand[1] > best[1]:
                best = cand
                end = cand[1]
        selected.append(best)
        cursor = best[1]

    # 4) Resolve ambiguities using mention counts of all unambiguous matches first.
    unamb_ids = Counter()
    for s, e, k in selected:
        cands = ld.inverted.get(k, [])
        if len(cands) == 1:
            unamb_ids[cands[0][0]] += 1

    mentions: list[dict[str, Any]] = []
    for s, e, k in selected:
        cands = ld.inverted.get(k, [])
        if not cands:
            continue
        cid, original = _resolve_ambiguous(cands, unamb_ids)
        # Map folded indices back to NFC indices
        nfc_start = idx_map[s] if s < len(idx_map) else len(nfc_text)
        nfc_end = idx_map[e - 1] + 1 if (e - 1) < len(idx_map) else len(nfc_text)
        surface = nfc_text[nfc_start:nfc_end]
        mentions.append(
            {
                "surface": surface,
                "start": nfc_start,
                "end": nfc_end,
                "chebi_id": cid,
            }
        )
    return mentions
