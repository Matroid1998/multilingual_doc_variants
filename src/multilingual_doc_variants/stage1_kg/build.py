"""Assemble data/kg/chebi_concepts.parquet.

Two-pass build:

1. Parse ChEBI + compute neighbor index.
2. Build a *bootstrap* per-language alias dictionary from ChEBI native synonyms,
   run stage 2's linker against the corpus to find which ChEBI ids are mentioned.
3. Expand that set with one hop of neighbor closures -> "relevant universe".
4. Crosswalk the relevant universe to Wikidata for Wikipedia titles.
5. Re-link the corpus with the augmented dictionary so anything that becomes findable
   via a Wikipedia title also enters the universe (one extra hop of neighbors then crawled).
6. Write the parquet keyed on the final relevant universe.
"""
from __future__ import annotations

from typing import Iterable

import polars as pl
from tqdm import tqdm

from ..config import CHEBI_CONCEPTS_PARQUET, LANGS
from ..corpus import load_rows
from .download_chebi import download_chebi
from .neighbors import NeighborIndex
from .parse_chebi import ChebiData, load_chebi
from .wikidata import crosswalk


def _dedup_preserve_order(items: Iterable[str], lang: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if not it:
            continue
        k = it.casefold() if lang != "zh" else it
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _build_aliases_combined(
    chebi: ChebiData, qid_titles: dict[str, dict]
) -> dict[str, dict[str, list[str]]]:
    """Per concept: dedup union of aliases_chebi and wikipedia_titles, by language."""
    combined: dict[str, dict[str, list[str]]] = {}
    all_ids: set[str] = set()
    all_ids.update(chebi.aliases_chebi.keys())
    all_ids.update(qid_titles.keys())
    for cid in all_ids:
        per_lang: dict[str, list[str]] = {}
        a = chebi.aliases_chebi.get(cid, {})
        wt = qid_titles.get(cid, {}).get("titles", {})
        for lang in LANGS:
            merged: list[str] = []
            merged.extend(a.get(lang, []) or [])
            t = wt.get(lang)
            if t:
                merged.append(t)
            per_lang[lang] = _dedup_preserve_order(merged, lang)
        combined[cid] = per_lang
    return combined


def _find_mentioned_ids(
    aliases_combined: dict[str, dict[str, list[str]]]
) -> set[str]:
    """Run the stage 2 linker (lazy import to avoid cycles) and return matched chebi_ids."""
    from ..stage2_link.dictionary import build_dictionaries
    from ..stage2_link.link import link_document

    dicts = build_dictionaries(aliases_combined)
    rows = load_rows()
    found: set[str] = set()
    for r in tqdm(rows, desc="[stage1] bootstrap linking"):
        mentions = link_document(r.text, r.language, dicts, aliases_combined)
        for m in mentions:
            found.add(m["chebi_id"])
    return found


def _per_lang_lists(d: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """Force a full {en, de, es, fr, zh} -> list[str] dict for parquet schema stability."""
    d = d or {}
    return {lg: list(d.get(lg, []) or []) for lg in LANGS}


def _per_lang_strs(d: dict[str, str] | None) -> dict[str, str | None]:
    """Force {en, de, es, fr, zh} -> str|None for parquet schema stability."""
    d = d or {}
    return {lg: d.get(lg) for lg in LANGS}


def _row_for_concept(
    cid: str,
    chebi: ChebiData,
    aliases_combined: dict[str, dict[str, list[str]]],
    qid_titles: dict[str, dict],
    neighbors: NeighborIndex,
) -> dict:
    return {
        "chebi_id": cid,
        "chebi_name_en": chebi.name_en.get(cid),
        "inchikey": chebi.inchikey.get(cid),
        "inchi": chebi.inchi.get(cid),
        "smiles": chebi.smiles.get(cid),
        "formula": chebi.formula.get(cid),
        "chebi_star": chebi.star.get(cid),
        "aliases_chebi": _per_lang_lists(chebi.aliases_chebi.get(cid)),
        "wikidata_qid": qid_titles.get(cid, {}).get("qid"),
        "wikipedia_titles": _per_lang_strs(qid_titles.get(cid, {}).get("titles")),
        "aliases_combined": _per_lang_lists(aliases_combined.get(cid)),
        "parents_isa": chebi.parents_isa.get(cid, []),
        "roles": chebi.has_role.get(cid, []),
        "siblings_isa": neighbors.siblings_isa(cid),
        "siblings_role": neighbors.siblings_role(cid),
        "conjugate_pair": neighbors.conjugate_pair(cid),
        "tautomers": neighbors.tautomers(cid),
        "stereo_or_tautomer_inchikey_block": neighbors.stereo_or_tautomer_inchikey_block(cid),
        "parent_hydride_family": neighbors.parent_hydride_family(cid),
    }


def build(
    refresh_download: bool = False,
    refresh_wikidata: bool = False,
    with_wikidata: bool = False,
) -> int:
    print("[stage1] downloading ChEBI flat files (idempotent)", flush=True)
    download_chebi(refresh=refresh_download)

    print("[stage1] parsing ChEBI", flush=True)
    chebi = load_chebi()
    neighbors = NeighborIndex(chebi)
    print(f"[stage1] ChEBI loaded: {len(chebi.name_en):,} terms", flush=True)

    # Pass 1: bootstrap dictionary from ChEBI native synonyms only
    bootstrap_aliases = _build_aliases_combined(chebi, qid_titles={})
    mentioned = _find_mentioned_ids(bootstrap_aliases)
    print(f"[stage1] bootstrap pass found {len(mentioned)} ChEBI ids in corpus", flush=True)

    qid_titles: dict[str, dict] = {}
    if with_wikidata:
        # Crosswalk only corpus-mentioned concepts (not neighbors).
        qid_titles = crosswalk(mentioned, refresh=refresh_wikidata)
        n_with_wp = sum(1 for r in qid_titles.values() if r.get("titles"))
        print(
            f"[stage1] Wikidata crosswalk pass 1: {n_with_wp}/{len(qid_titles)} have Wikipedia titles",
            flush=True,
        )

        augmented_aliases = _build_aliases_combined(chebi, qid_titles)
        mentioned2 = _find_mentioned_ids(augmented_aliases)
        print(f"[stage1] augmented pass found {len(mentioned2)} ChEBI ids in corpus", flush=True)

        new_for_wd = mentioned2 - set(qid_titles.keys())
        if new_for_wd:
            print(f"[stage1] fetching Wikidata for {len(new_for_wd)} newly-mentioned ids", flush=True)
            qid_titles.update(crosswalk(new_for_wd, refresh=False))
            augmented_aliases = _build_aliases_combined(chebi, qid_titles)
        relevant = mentioned2
    else:
        print("[stage1] Wikidata crosswalk skipped (pass --with-wikidata to enable)", flush=True)
        augmented_aliases = bootstrap_aliases
        relevant = mentioned

    print(f"[stage1] final relevant universe: {len(relevant)} ids", flush=True)

    rows = [
        _row_for_concept(cid, chebi, augmented_aliases, qid_titles, neighbors)
        for cid in sorted(relevant)
    ]
    df = pl.DataFrame(rows)
    CHEBI_CONCEPTS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(CHEBI_CONCEPTS_PARQUET)
    print(f"[stage1] wrote {CHEBI_CONCEPTS_PARQUET} with {len(rows)} concepts", flush=True)
    return len(rows)
