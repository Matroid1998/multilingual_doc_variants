"""Idempotent ChEBI flat-file downloads with Last-Modified provenance.

NOTE: ChEBI's FTP layout changed (Flat_file_tab_delimited/ -> flat_files/) and several
files are now gzipped TSVs with lowercase column names. We pull the current layout.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from tqdm import tqdm

from ..config import CHEBI_RAW_DIR, CHEBI_RELEASE_METADATA

FTP_BASE_FLAT = "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files"
FTP_BASE_ONTO = "https://ftp.ebi.ac.uk/pub/databases/chebi/ontology"

FILES = {
    "chebi.obo": f"{FTP_BASE_ONTO}/chebi.obo",
    "names.tsv.gz": f"{FTP_BASE_FLAT}/names.tsv.gz",
    "compounds.tsv.gz": f"{FTP_BASE_FLAT}/compounds.tsv.gz",
    "chemical_data.tsv.gz": f"{FTP_BASE_FLAT}/chemical_data.tsv.gz",
    "structures.tsv.gz": f"{FTP_BASE_FLAT}/structures.tsv.gz",
    "relation.tsv.gz": f"{FTP_BASE_FLAT}/relation.tsv.gz",
    "relation_type.tsv.gz": f"{FTP_BASE_FLAT}/relation_type.tsv.gz",
}


def _download_one(url: str, out_path: Path, client: httpx.Client) -> str | None:
    with client.stream("GET", url, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", "0")) or None
        last_mod = resp.headers.get("Last-Modified")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=out_path.name, leave=False
        ) as bar:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                f.write(chunk)
                bar.update(len(chunk))
    return last_mod


def download_chebi(refresh: bool = False) -> dict[str, str | None]:
    """Download ChEBI flat files into CHEBI_RAW_DIR. Returns Last-Modified per file."""
    CHEBI_RAW_DIR.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, str | None] = {}
    if CHEBI_RELEASE_METADATA.exists() and not refresh:
        metadata = json.loads(CHEBI_RELEASE_METADATA.read_text())
    with httpx.Client(headers={"User-Agent": "multilingual-doc-variants/0.1"}) as client:
        for name, url in FILES.items():
            out_path = CHEBI_RAW_DIR / name
            if out_path.exists() and not refresh:
                continue
            print(f"[stage1] downloading {name} ...")
            last_mod = _download_one(url, out_path, client)
            metadata[name] = last_mod
    CHEBI_RELEASE_METADATA.write_text(json.dumps(metadata, indent=2))
    return metadata
