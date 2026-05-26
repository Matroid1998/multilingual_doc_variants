"""Parse ChEBI flat files into in-memory structures.

Adapted to the *current* ChEBI flat_files schema (lowercase columns, numeric relation_type ids).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import obonet
import polars as pl

from ..config import CHEBI_RAW_DIR, LANGS

# status_id values: 1=CHECKED, 3=OK, 9=SUBMITTED. We keep checked + ok by default.
_CURRENT_STATUS_IDS = {1, 3}


def _normalize_id(raw) -> str | None:
    """ChEBI id from any form -> canonical 'CHEBI:nnn'."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "null":
        return None
    if s.startswith("CHEBI:"):
        return s
    if s.lstrip("-").isdigit():
        return f"CHEBI:{s}"
    return None


@dataclass
class ChebiData:
    """In-memory ChEBI slice — every concept keyed by canonical 'CHEBI:nnn'."""

    name_en: dict[str, str] = field(default_factory=dict)
    star: dict[str, int] = field(default_factory=dict)
    parent_id: dict[str, str | None] = field(default_factory=dict)

    aliases_chebi: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # aliases_chebi[chebi_id][lang] = [name, ...]

    inchi: dict[str, str | None] = field(default_factory=dict)
    inchikey: dict[str, str | None] = field(default_factory=dict)
    smiles: dict[str, str | None] = field(default_factory=dict)
    formula: dict[str, str | None] = field(default_factory=dict)

    parents_isa: dict[str, list[str]] = field(default_factory=dict)
    children_isa: dict[str, list[str]] = field(default_factory=dict)
    has_role: dict[str, list[str]] = field(default_factory=dict)
    has_parent_hydride: dict[str, list[str]] = field(default_factory=dict)
    is_conjugate_acid_of: dict[str, list[str]] = field(default_factory=dict)
    is_conjugate_base_of: dict[str, list[str]] = field(default_factory=dict)
    is_tautomer_of: dict[str, list[str]] = field(default_factory=dict)

    def all_ids(self) -> Iterator[str]:
        seen: set[str] = set()
        for d in (self.name_en, self.aliases_chebi, self.parents_isa, self.has_role, self.inchi):
            for k in d:
                if k not in seen:
                    seen.add(k)
                    yield k


def parse_ontology(obo_path: Path) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    """OBO -> (name_en, parents_isa, children_isa)."""
    graph = obonet.read_obo(obo_path)
    name_en: dict[str, str] = {}
    parents: dict[str, list[str]] = defaultdict(list)
    children: dict[str, list[str]] = defaultdict(list)
    for node_id, data in graph.nodes(data=True):
        nid = _normalize_id(node_id)
        if nid is None:
            continue
        if "name" in data:
            name_en[nid] = data["name"]
    for src, dst, key in graph.edges(keys=True):
        if key == "is_a":
            c = _normalize_id(src)
            p = _normalize_id(dst)
            if c and p:
                parents[c].append(p)
                children[p].append(c)
    return name_en, dict(parents), dict(children)


def _read_tsv(path: Path) -> pl.DataFrame:
    return pl.read_csv(path, separator="\t", infer_schema_length=0, ignore_errors=True)


def parse_compounds(path: Path) -> tuple[dict[str, str], dict[str, int], dict[str, str | None]]:
    """compounds.tsv.gz columns: id, name, status_id, source, parent_id, merge_type, chebi_accession, definition, ascii_name, stars, modified_on, release_date"""
    name_en: dict[str, str] = {}
    star: dict[str, int] = {}
    parent_id: dict[str, str | None] = {}
    df = _read_tsv(path)
    for row in df.iter_rows(named=True):
        cid = _normalize_id(row.get("chebi_accession") or row.get("id"))
        if cid is None:
            continue
        nm = row.get("name")
        if nm and nm != "null":
            name_en[cid] = nm
        st = (row.get("stars") or "").strip()
        if st.isdigit():
            star[cid] = int(st)
        pid_raw = (row.get("parent_id") or "").strip()
        # parent_id stores the integer id of the parent compound (when this concept is a child of a generic)
        parent_id[cid] = _normalize_id(pid_raw) if pid_raw else None
    return name_en, star, parent_id


def parse_names(path: Path) -> dict[str, dict[str, list[str]]]:
    """names.tsv.gz columns: id, compound_id, name, type, status_id, adapted, language_code, ascii_name"""
    out: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    df = _read_tsv(path)
    for row in df.iter_rows(named=True):
        cid = _normalize_id(row.get("compound_id"))
        name = row.get("name")
        lang = (row.get("language_code") or "").strip().lower() or "en"
        if cid is None or not name or name == "null":
            continue
        if lang not in LANGS:
            continue
        out[cid][lang].append(name)
    final: dict[str, dict[str, list[str]]] = {}
    for cid, by_lang in out.items():
        final[cid] = {}
        for lang, names in by_lang.items():
            seen: set[str] = set()
            dedup: list[str] = []
            for n in names:
                k = n.casefold() if lang != "zh" else n
                if k not in seen:
                    seen.add(k)
                    dedup.append(n)
            final[cid][lang] = dedup
    return final


def parse_structures(path: Path) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, str | None]]:
    """structures.tsv.gz columns: id, compound_id, status_id, molfile, smiles, standard_inchi, standard_inchi_key, dimension, default_structure

    A compound may have multiple structures (different molfiles / 2D vs 3D). Pick the default one
    (`default_structure = T`) when present, else the first row.
    """
    inchi: dict[str, str | None] = {}
    inchikey: dict[str, str | None] = {}
    smiles: dict[str, str | None] = {}
    df = _read_tsv(path)
    for row in df.iter_rows(named=True):
        cid = _normalize_id(row.get("compound_id"))
        if cid is None:
            continue
        default_flag = (row.get("default_structure") or "").strip().upper() in ("T", "TRUE", "1")
        inchi_val = (row.get("standard_inchi") or "").strip() or None
        inchikey_val = (row.get("standard_inchi_key") or "").strip() or None
        smiles_val = (row.get("smiles") or "").strip() or None
        # Prefer the default-structure row; otherwise take first seen.
        if default_flag or cid not in inchi:
            if inchi_val is not None:
                inchi[cid] = inchi_val
            if inchikey_val is not None:
                inchikey[cid] = inchikey_val
            if smiles_val is not None:
                smiles[cid] = smiles_val
    return inchi, inchikey, smiles


def parse_chemical_data(path: Path) -> dict[str, str | None]:
    """chemical_data.tsv.gz columns: id, compound_id, formula, charge, mass, monoisotopic_mass, status_id, structure_id, is_autogenerated"""
    formula: dict[str, str | None] = {}
    df = _read_tsv(path)
    for row in df.iter_rows(named=True):
        cid = _normalize_id(row.get("compound_id"))
        if cid is None:
            continue
        f = (row.get("formula") or "").strip()
        if not f:
            continue
        if cid not in formula:
            formula[cid] = f
    return formula


_REL_CODES_OF_INTEREST = {
    "has_role",
    "has_parent_hydride",
    "is_conjugate_acid_of",
    "is_conjugate_base_of",
    "is_tautomer_of",
}


def parse_relations(path: Path, relation_type_path: Path) -> dict[str, dict[str, list[str]]]:
    """relation.tsv.gz columns: id, relation_type_id, init_id, final_id, status_id, evidence_accession, evidence_source_id

    Requires relation_type.tsv.gz to map numeric relation_type_id -> string code.
    """
    rt_df = _read_tsv(relation_type_path)
    code_by_id: dict[str, str] = {}
    for row in rt_df.iter_rows(named=True):
        rid = (row.get("id") or "").strip()
        code = (row.get("code") or "").strip()
        if rid and code:
            code_by_id[rid] = code

    out: dict[str, dict[str, list[str]]] = {t: defaultdict(list) for t in _REL_CODES_OF_INTEREST}
    rel_df = _read_tsv(path)
    for row in rel_df.iter_rows(named=True):
        rt_id = (row.get("relation_type_id") or "").strip()
        code = code_by_id.get(rt_id)
        if code not in _REL_CODES_OF_INTEREST:
            continue
        status_id_str = (row.get("status_id") or "").strip()
        status_id = int(status_id_str) if status_id_str.isdigit() else None
        if status_id is not None and status_id not in _CURRENT_STATUS_IDS:
            continue
        init_cid = _normalize_id(row.get("init_id"))
        final_cid = _normalize_id(row.get("final_id"))
        if init_cid is None or final_cid is None:
            continue
        out[code][init_cid].append(final_cid)
    return {t: dict(d) for t, d in out.items()}


def load_chebi(raw_dir: Path = CHEBI_RAW_DIR) -> ChebiData:
    obo_name_en, parents_isa, children_isa = parse_ontology(raw_dir / "chebi.obo")
    cmp_name_en, star, parent_id = parse_compounds(raw_dir / "compounds.tsv.gz")
    aliases = parse_names(raw_dir / "names.tsv.gz")
    inchi, inchikey, smiles = parse_structures(raw_dir / "structures.tsv.gz")
    formula = parse_chemical_data(raw_dir / "chemical_data.tsv.gz")
    rels = parse_relations(raw_dir / "relation.tsv.gz", raw_dir / "relation_type.tsv.gz")

    # Prefer compounds.tsv name where available, otherwise OBO
    name_en = {**obo_name_en, **cmp_name_en}

    return ChebiData(
        name_en=name_en,
        star=star,
        parent_id=parent_id,
        aliases_chebi={cid: dict(by_lang) for cid, by_lang in aliases.items()},
        inchi=inchi,
        inchikey=inchikey,
        smiles=smiles,
        formula=formula,
        parents_isa=parents_isa,
        children_isa=children_isa,
        has_role=rels.get("has_role", {}),
        has_parent_hydride=rels.get("has_parent_hydride", {}),
        is_conjugate_acid_of=rels.get("is_conjugate_acid_of", {}),
        is_conjugate_base_of=rels.get("is_conjugate_base_of", {}),
        is_tautomer_of=rels.get("is_tautomer_of", {}),
    )
