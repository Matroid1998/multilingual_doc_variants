"""Rule-based orthographic perturbations (no LLM)."""
from __future__ import annotations

import random
import unicodedata


GREEK_TO_ASCII = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron",
    "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}
ASCII_TO_SHORT = {"alpha": "a", "beta": "b", "gamma": "g", "delta": "d"}

SUB_DIGIT = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
             "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9"}
SUP_DIGIT = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
             "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}


def _hyphenation_swap(s: str, rng: random.Random) -> str:
    if "-" in s and rng.random() < 0.5:
        return s.replace("-", "", 1)
    # Insert a hyphen at a random word interior position
    if len(s) >= 4:
        pos = rng.randint(1, len(s) - 2)
        return s[:pos] + "-" + s[pos:]
    return s


def _greek_swap(s: str, rng: random.Random) -> str:
    out_chars: list[str] = []
    swapped = False
    for ch in s:
        if ch in GREEK_TO_ASCII and not swapped:
            expansion = GREEK_TO_ASCII[ch]
            if rng.random() < 0.5 and expansion in ASCII_TO_SHORT:
                expansion = ASCII_TO_SHORT[expansion]
            out_chars.append(expansion)
            swapped = True
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def _oxidation_swap(s: str, rng: random.Random) -> str:
    # Cheap detection: '(III)' style or 'X3+' style
    import re
    m = re.search(r"\(([IVX]+)\)", s)
    if m:
        roman = m.group(1)
        roman_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}
        if roman in roman_map:
            return s[: m.start()] + f"{roman_map[roman]}+" + s[m.end():]
    m = re.search(r"(\d)\+", s)
    if m:
        digit = m.group(1)
        roman_inv = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"}
        if int(digit) in roman_inv:
            return s[: m.start()] + f"({roman_inv[int(digit)]})" + s[m.end():]
    return s


def _locant_whitespace(s: str, rng: random.Random) -> str:
    return s.replace(",", " ", 1) if "," in s else s


def _scriptlevel_loss(s: str, rng: random.Random) -> str:
    out = []
    for ch in s:
        out.append(SUB_DIGIT.get(ch, SUP_DIGIT.get(ch, ch)))
    return "".join(out)


def _typo(s: str, rng: random.Random) -> str:
    out = list(s)
    n_typos = max(1, len(s) // 20)
    for _ in range(n_typos):
        if not out:
            break
        i = rng.randint(0, len(out) - 1)
        op = rng.choice(("delete", "swap"))
        if op == "delete" and len(out) > 1:
            del out[i]
        elif op == "swap" and i < len(out) - 1:
            out[i], out[i + 1] = out[i + 1], out[i]
    return "".join(out)


def _case_noise(s: str, rng: random.Random) -> str:
    return "".join(ch.upper() if rng.random() < 0.3 else ch for ch in s)


_PERTURBATIONS = [
    _hyphenation_swap,
    _greek_swap,
    _oxidation_swap,
    _locant_whitespace,
    _scriptlevel_loss,
    _typo,
    _case_noise,
]


def perturb(term: str, seed: int) -> str:
    """Apply 1-2 sampled perturbations to `term` and return the new form.

    Guarantees the output differs from the input by falling back through additional
    perturbations until something changes; final fallback is case noise on any letter.
    """
    rng = random.Random(seed)
    term_nfc = unicodedata.normalize("NFC", term)

    # First pass: try 1-2 randomly sampled perturbations
    n = rng.randint(1, 2)
    perts = rng.sample(_PERTURBATIONS, k=min(n, len(_PERTURBATIONS)))
    out = term_nfc
    for p in perts:
        new = p(out, rng)
        if new and new != out:
            out = new

    if out != term_nfc:
        return out

    # Second pass: try every perturbation in turn until one changes the term
    for p in _PERTURBATIONS:
        new = p(out, rng)
        if new and new != out:
            return new

    # Last resort: flip the case of the first alphabetic character
    chars = list(term_nfc)
    for i, ch in enumerate(chars):
        if ch.isalpha():
            chars[i] = ch.upper() if ch.islower() else ch.lower()
            break
    return "".join(chars)
