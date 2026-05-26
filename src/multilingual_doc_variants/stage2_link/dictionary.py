"""Per-language Aho-Corasick automata over surface forms -> {chebi_id, original}."""
from __future__ import annotations

from dataclasses import dataclass

import ahocorasick

from ..config import LANGS, MIN_ALIAS_LEN, MIN_ALIAS_LEN_ZH
from ..io_utils import fold


@dataclass
class LangDict:
    lang: str
    automaton: ahocorasick.Automaton | None  # None when no aliases registered
    # surface_form (folded) -> list of (chebi_id, original_alias)
    inverted: dict[str, list[tuple[str, str]]]


def _min_len(lang: str) -> int:
    return MIN_ALIAS_LEN_ZH if lang == "zh" else MIN_ALIAS_LEN


def build_dictionaries(
    aliases_combined: dict[str, dict[str, list[str]]],
) -> dict[str, LangDict]:
    out: dict[str, LangDict] = {}
    for lang in LANGS:
        inverted: dict[str, list[tuple[str, str]]] = {}
        for cid, by_lang in aliases_combined.items():
            for original in by_lang.get(lang, []) or []:
                if len(original) < _min_len(lang):
                    continue
                key = fold(original, lang)
                if len(key) < _min_len(lang):
                    continue
                inverted.setdefault(key, []).append((cid, original))
        if not inverted:
            out[lang] = LangDict(lang=lang, automaton=None, inverted=inverted)
            continue
        a = ahocorasick.Automaton()
        for key in inverted:
            a.add_word(key, key)
        a.make_automaton()
        out[lang] = LangDict(lang=lang, automaton=a, inverted=inverted)
    return out
