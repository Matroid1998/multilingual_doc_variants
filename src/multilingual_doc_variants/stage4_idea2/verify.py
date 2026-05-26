"""Post-rewrite verification checks (spec §4.6)."""
from __future__ import annotations

from dataclasses import dataclass

from ..io_utils import nfc
from .llm import LLMClient


@dataclass
class VerifyResult:
    ok: bool
    round_trip_flag: bool
    swap_char_offset: int | None
    rejection_reason: str | None = None


def term_presence_offset(rewrite: str, swap_term: str) -> int | None:
    """Return offset in NFC(rewrite) iff swap_term appears exactly once (case-insensitive); else None.

    Uses regex case-insensitive scan so the returned index lines up with the *original* text
    (NFC, not casefolded), which is what downstream offset users expect.
    """
    import regex as re_mod
    rew = nfc(rewrite)
    t = nfc(swap_term)
    if not t:
        return None
    pattern = re_mod.compile(re_mod.escape(t), flags=re_mod.IGNORECASE)
    matches = list(pattern.finditer(rew))
    if len(matches) != 1:
        return None
    return matches[0].start()


def source_term_absent_at_swap(rewrite: str, original_surface: str, swap_offset: int, window: int = 80) -> bool:
    """Check the source surface form is not present near the swap site."""
    rew = nfc(rewrite)
    orig = nfc(original_surface)
    if not orig:
        return True
    lo = max(0, swap_offset - window)
    hi = min(len(rew), swap_offset + len(orig) + window)
    return orig not in rew[lo:hi] or rew[lo:hi].count(orig) == 0


def semantic_preserved(
    client: LLMClient,
    original_text: str,
    rewritten_text: str,
    original_term: str,
    swap_term: str,
    *,
    intent: str = "translation",
) -> bool:
    """Verify the rewrite preserves the original meaning UP TO the single term substitution.

    `intent` describes what kind of substitution was made so the verifier doesn't conflate
    a deliberately-noisy term (variant D) with a meaning change:
      - "translation" : swap_term is a different-language equivalent of original_term
      - "perturbation": swap_term is a same-language orthographic noise version of original_term
      - "noun_swap"   : swap_term is a translation of a non-chemistry noun
    """
    intent_clause = {
        "translation": (
            "The rewrite intentionally substitutes one chemistry term with its equivalent term in "
            "another language. The rest of the passage stays in the original language."
        ),
        "perturbation": (
            "The rewrite intentionally introduces orthographic noise (a typo, hyphenation, case noise, "
            "or similar) into a single chemistry term, in the same language as the rest of the passage. "
            "The noisy term should still be recognizable as referring to the same concept."
        ),
        "noun_swap": (
            "The rewrite intentionally substitutes a single non-chemistry common noun with its "
            "equivalent in another language; the rest of the passage stays in the original language."
        ),
    }.get(intent, "The rewrite intentionally substitutes one term.")
    system = (
        "You are a careful bilingual editor. You will be shown an ORIGINAL passage and a REWRITTEN passage. "
        f"{intent_clause} Decide whether the rewrite preserves the meaning of the original UP TO this "
        "single substitution. Reply with exactly YES or NO on the first line."
    )
    user = (
        f"ORIGINAL TERM: {original_term}\n"
        f"SUBSTITUTED TERM: {swap_term}\n\n"
        f"--- ORIGINAL ---\n{original_text}\n\n"
        f"--- REWRITTEN ---\n{rewritten_text}\n"
    )
    reply = client.complete(system=system, user=user, temperature=0.0, max_output_tokens=10)
    return reply.strip().upper().startswith("YES")


def round_trip(
    client: LLMClient,
    rewritten_text: str,
    swap_term: str,
    swap_lang: str,
    source_lang: str,
    original_term: str,
) -> bool:
    """Return True iff back-translation matches original_term (case-insensitive substring); else False."""
    system = (
        f"You translate technical chemistry terms. The user provides a passage in mixed languages and one term in "
        f"{swap_lang}. Output ONLY the equivalent term in {source_lang}, no quotes, no explanation."
    )
    user = f"Passage:\n{rewritten_text}\n\nTerm to translate ({swap_lang}): {swap_term}"
    reply = client.complete(system=system, user=user, temperature=0.0, max_output_tokens=40).strip()
    return reply.casefold() in original_term.casefold() or original_term.casefold() in reply.casefold()
