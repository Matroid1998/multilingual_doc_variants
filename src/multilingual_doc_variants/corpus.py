"""Corpus loader: full-text concatenation + L_avail computation."""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import CORPUS_CSV

# Order matters: character offsets used downstream are relative to this join.
TEXT_FIELDS = ("title", "abstract", "description", "first_claim", "context")
JOIN_SEP = "\n"


@dataclass
class CorpusRow:
    id: str
    publication_number: str
    language: str
    title: str
    abstract: str
    description: str
    first_claim: str
    context: str
    text: str  # concatenated full document text

    @property
    def field_spans(self) -> dict[str, tuple[int, int]]:
        """Per-field [start, end) character spans within `text`."""
        spans: dict[str, tuple[int, int]] = {}
        cursor = 0
        for i, field in enumerate(TEXT_FIELDS):
            value: str = getattr(self, field)
            start = cursor
            end = cursor + len(value)
            spans[field] = (start, end)
            cursor = end
            if i < len(TEXT_FIELDS) - 1:
                cursor += len(JOIN_SEP)
        return spans


def _concat(row: dict) -> str:
    parts = [(row.get(f) or "") for f in TEXT_FIELDS]
    return JOIN_SEP.join(parts)


def load_rows(csv_path: Path = CORPUS_CSV) -> list[CorpusRow]:
    out: list[CorpusRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(
                CorpusRow(
                    id=row["id"],
                    publication_number=row["publication_number"],
                    language=row["language"],
                    title=row.get("title") or "",
                    abstract=row.get("abstract") or "",
                    description=row.get("description") or "",
                    first_claim=row.get("first_claim") or "",
                    context=row.get("context") or "",
                    text=_concat(row),
                )
            )
    return out


def iter_rows(csv_path: Path = CORPUS_CSV) -> Iterator[CorpusRow]:
    yield from load_rows(csv_path)


def compute_l_avail(rows: list[CorpusRow]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        out[r.publication_number].add(r.language)
    return dict(out)
