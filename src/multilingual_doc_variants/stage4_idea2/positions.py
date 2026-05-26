"""Locate title / first_sentence / body offset ranges in the concatenated text.

Mirrors the join in corpus.py exactly: '\n'.join([title, abstract, description, first_claim, context]).
"""
from __future__ import annotations

from dataclasses import dataclass

import pysbd

from ..corpus import JOIN_SEP, TEXT_FIELDS


@dataclass
class PositionRanges:
    title: tuple[int, int] | None
    first_sentence: tuple[int, int] | None
    body: list[tuple[int, int]]  # may be multiple disjoint spans


_SBD_LANG_FALLBACK = {"en", "de", "es", "fr", "zh"}


def _first_sentence_span(text_field: str, lang: str) -> tuple[int, int] | None:
    if not text_field.strip():
        return None
    try:
        seg = pysbd.Segmenter(language=lang if lang in _SBD_LANG_FALLBACK else "en", clean=False)
        spans = seg.segment(text_field)
        if not spans:
            return None
        first = spans[0]
        idx = text_field.find(first)
        if idx < 0:
            return None
        return (idx, idx + len(first))
    except Exception:
        # Fallback: split on first sentence-ending punctuation
        for i, ch in enumerate(text_field):
            if ch in ".!?。！？":
                return (0, i + 1)
        return (0, len(text_field))


def compute_position_ranges(
    title: str,
    abstract: str,
    description: str,
    first_claim: str,
    context: str,
    lang: str,
) -> PositionRanges:
    """Compute offsets into the concatenated 'text' string (per corpus.py join)."""
    fields = {
        "title": title,
        "abstract": abstract,
        "description": description,
        "first_claim": first_claim,
        "context": context,
    }
    # Per-field spans within concatenated text
    field_spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for i, name in enumerate(TEXT_FIELDS):
        val = fields[name]
        field_spans[name] = (cursor, cursor + len(val))
        cursor += len(val)
        if i < len(TEXT_FIELDS) - 1:
            cursor += len(JOIN_SEP)

    # Title
    t_start, t_end = field_spans["title"]
    title_range = (t_start, t_end) if title.strip() else None

    # First sentence: pick first non-empty among abstract, description
    fs_field = None
    fs_text = None
    for cand in ("abstract", "description"):
        if fields[cand].strip():
            fs_field = cand
            fs_text = fields[cand]
            break
    first_sentence_range = None
    if fs_field and fs_text:
        rel = _first_sentence_span(fs_text, lang)
        if rel is not None:
            base = field_spans[fs_field][0]
            first_sentence_range = (base + rel[0], base + rel[1])

    # Body: union of (description, first_claim, context) past the first sentence
    body: list[tuple[int, int]] = []
    for name in ("description", "first_claim", "context"):
        seg_start, seg_end = field_spans[name]
        if seg_start == seg_end:
            continue
        # If first_sentence is inside this segment, body starts after it; otherwise full segment
        if first_sentence_range and seg_start <= first_sentence_range[0] < seg_end:
            body.append((first_sentence_range[1], seg_end))
        else:
            body.append((seg_start, seg_end))
    body = [(s, e) for s, e in body if e > s]

    return PositionRanges(title=title_range, first_sentence=first_sentence_range, body=body)


def offset_position(offset: int, ranges: PositionRanges) -> str | None:
    """Classify an absolute character offset as 'title' | 'first_sentence' | 'body' | None."""
    if ranges.title and ranges.title[0] <= offset < ranges.title[1]:
        return "title"
    if ranges.first_sentence and ranges.first_sentence[0] <= offset < ranges.first_sentence[1]:
        return "first_sentence"
    for s, e in ranges.body:
        if s <= offset < e:
            return "body"
    return None
