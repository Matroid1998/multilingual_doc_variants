"""Filesystem + normalization helpers shared across stages."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Iterable, Iterator


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def fold(s: str, lang: str) -> str:
    """NFC + casefold for Latin-script languages; NFC-only for zh."""
    n = nfc(s)
    return n if lang == "zh" else n.casefold()


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False))
        f.write("\n")
