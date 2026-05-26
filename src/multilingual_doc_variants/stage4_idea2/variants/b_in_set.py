"""Variant B: in-set code-switch. Helpers — swap-language picker + parallel-row context."""
from __future__ import annotations

import random


def pick_swap_lang_in_set(l_avail: set[str], source_lang: str, seed: int) -> str | None:
    pool = sorted(l_avail - {source_lang})
    if not pool:
        return None
    return random.Random(seed).choice(pool)


def parallel_context_snippet(
    chebi_id: str,
    parallel_row: dict | None,
    window: int = 200,
    max_snippets: int = 2,
) -> str | None:
    """Extract up to `max_snippets` short windows around mentions of `chebi_id` in the
    parallel-language row, so the LLM can see how the term is rendered in the actual
    translation. Returns a single newline-joined string, or None if no such mentions."""
    if parallel_row is None:
        return None
    text = parallel_row.get("text") or ""
    mentions = [m for m in (parallel_row.get("mentions") or []) if m.get("chebi_id") == chebi_id]
    if not mentions:
        return None
    snippets: list[str] = []
    for m in mentions[:max_snippets]:
        s, e = m["start"], m["end"]
        lo = max(0, s - window)
        hi = min(len(text), e + window)
        snippets.append(text[lo:hi])
    label = f"PARALLEL-TRANSLATION CONTEXT (the same document in the target language, showing how the term is rendered):\n"
    return label + "\n---\n".join(snippets)
