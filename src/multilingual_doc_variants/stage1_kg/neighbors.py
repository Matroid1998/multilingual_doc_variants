"""Compute confusable-neighbor closures over the loaded ChEBI graph."""
from __future__ import annotations

from collections import defaultdict

from .parse_chebi import ChebiData


def siblings_via_isa(c: str, data: ChebiData) -> list[str]:
    out: set[str] = set()
    for p in data.parents_isa.get(c, []):
        for sib in data.children_isa.get(p, []):
            if sib != c:
                out.add(sib)
    return sorted(out)


def _index_by_role(data: ChebiData) -> dict[str, set[str]]:
    by_role: dict[str, set[str]] = defaultdict(set)
    for cid, roles in data.has_role.items():
        for r in roles:
            by_role[r].add(cid)
    return by_role


def _index_by_parent_hydride(data: ChebiData) -> dict[str, set[str]]:
    by_hyd: dict[str, set[str]] = defaultdict(set)
    for cid, hyds in data.has_parent_hydride.items():
        for h in hyds:
            by_hyd[h].add(cid)
    return by_hyd


def _index_by_inchikey_block(data: ChebiData) -> dict[str, set[str]]:
    """First 14 chars of InChIKey -> set of chebi_ids."""
    by_block: dict[str, set[str]] = defaultdict(set)
    for cid, key in data.inchikey.items():
        if key and len(key) >= 14:
            by_block[key[:14]].add(cid)
    return by_block


class NeighborIndex:
    """Lazy lookups over pre-built reverse indexes."""

    def __init__(self, data: ChebiData):
        self.data = data
        self._role_idx = _index_by_role(data)
        self._hyd_idx = _index_by_parent_hydride(data)
        self._inchikey_idx = _index_by_inchikey_block(data)

    def siblings_isa(self, c: str) -> list[str]:
        return siblings_via_isa(c, self.data)

    def siblings_role(self, c: str) -> list[str]:
        out: set[str] = set()
        for r in self.data.has_role.get(c, []):
            for sib in self._role_idx.get(r, ()):
                if sib != c:
                    out.add(sib)
        return sorted(out)

    def conjugate_pair(self, c: str) -> list[str]:
        out: set[str] = set()
        out.update(self.data.is_conjugate_acid_of.get(c, []))
        out.update(self.data.is_conjugate_base_of.get(c, []))
        out.discard(c)
        return sorted(out)

    def tautomers(self, c: str) -> list[str]:
        return sorted({t for t in self.data.is_tautomer_of.get(c, []) if t != c})

    def stereo_or_tautomer_inchikey_block(self, c: str) -> list[str]:
        key = self.data.inchikey.get(c)
        if not key or len(key) < 14:
            return []
        cohort = self._inchikey_idx.get(key[:14], set())
        return sorted(x for x in cohort if x != c)

    def parent_hydride_family(self, c: str) -> list[str]:
        out: set[str] = set()
        for h in self.data.has_parent_hydride.get(c, []):
            for sib in self._hyd_idx.get(h, ()):
                if sib != c:
                    out.add(sib)
        return sorted(out)

    def all_neighbors(self, c: str) -> set[str]:
        """Union of every neighbor relation. Used to expand the relevant ChEBI universe."""
        out: set[str] = set()
        out.update(self.siblings_isa(c))
        out.update(self.siblings_role(c))
        out.update(self.conjugate_pair(c))
        out.update(self.tautomers(c))
        out.update(self.stereo_or_tautomer_inchikey_block(c))
        out.update(self.parent_hydride_family(c))
        return out
