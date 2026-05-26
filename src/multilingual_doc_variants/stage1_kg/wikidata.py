"""Wikidata SPARQL crosswalk: ChEBI ID (via wdt:P683) -> per-language Wikipedia article titles."""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import unquote

import httpx
from tqdm import tqdm

from ..config import LANGS, WIKIDATA_CACHE_JSON

SPARQL_URL = "https://query.wikidata.org/sparql"
BATCH_SIZE = 50
SLEEP_BETWEEN_BATCHES = 2.0
MAX_RETRIES = 7


def _build_query(numeric_ids: list[str]) -> str:
    values = " ".join(f'"{n}"' for n in numeric_ids)
    lang_filter = ",".join(f'"{lg}"' for lg in LANGS)
    return f"""
SELECT ?chebi ?qid ?article ?lang WHERE {{
  VALUES ?chebi {{ {values} }}
  ?qid wdt:P683 ?chebi .
  ?article schema:about ?qid ;
           schema:inLanguage ?lang ;
           schema:isPartOf ?wiki .
  FILTER(?lang IN ({lang_filter}))
}}
""".strip()


def _qid_short(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _title_from_url(url: str) -> str:
    # Wikipedia article URLs look like https://en.wikipedia.org/wiki/Water
    return unquote(url.rsplit("/", 1)[-1]).replace("_", " ")


def query_batch(client: httpx.Client, numeric_ids: list[str]) -> list[dict]:
    """One SPARQL request, returns parsed bindings."""
    payload = {"query": _build_query(numeric_ids), "format": "json"}
    last_status = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(
                SPARQL_URL,
                params=payload,
                headers={
                    "User-Agent": "multilingual-doc-variants/0.1 (research; mehdi.astaraki98@gmail.com)",
                    "Accept": "application/sparql-results+json",
                },
                timeout=120,
            )
        except (httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError) as e:
            backoff = min(60, 2 ** (attempt + 2))
            print(f"[stage1] wikidata transient error ({e!r}); backing off {backoff}s")
            time.sleep(backoff)
            continue
        last_status = resp.status_code
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            backoff = float(retry_after) if retry_after and retry_after.isdigit() else min(60, 2 ** (attempt + 2))
            print(f"[stage1] wikidata 429; backing off {backoff}s")
            time.sleep(backoff)
            continue
        if resp.status_code >= 500:
            backoff = min(60, 2 ** (attempt + 2))
            print(f"[stage1] wikidata {resp.status_code}; backing off {backoff}s")
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        return resp.json().get("results", {}).get("bindings", [])
    # Don't fail the whole run on one stubborn batch — log and skip.
    print(f"[stage1] WARN wikidata batch failed (last status={last_status}); skipping {len(numeric_ids)} ids")
    return []


def crosswalk(
    chebi_ids: set[str],
    cache_path: Path = WIKIDATA_CACHE_JSON,
    refresh: bool = False,
) -> dict[str, dict]:
    """
    Returns mapping: chebi_id (canonical 'CHEBI:nnn') -> {
        'qid': 'Q...' | None,
        'titles': {lang: title, ...}
    }

    Caches results to disk so reruns are offline.
    """
    cache: dict[str, dict] = {}
    if cache_path.exists() and not refresh:
        cache = json.loads(cache_path.read_text())

    pending = sorted(c for c in chebi_ids if c not in cache)
    if not pending:
        return {c: cache[c] for c in chebi_ids if c in cache}

    # Convert canonical 'CHEBI:nnn' -> bare numeric for VALUES clause
    def to_num(cid: str) -> str:
        return cid.split(":", 1)[1]
    num_to_canon = {to_num(c): c for c in pending}
    nums = list(num_to_canon)

    with httpx.Client() as client:
        for i in tqdm(range(0, len(nums), BATCH_SIZE), desc="[stage1] wikidata SPARQL"):
            batch = nums[i : i + BATCH_SIZE]
            bindings = query_batch(client, batch)
            # Initialize empty result for every queried id
            batch_results: dict[str, dict] = {
                num_to_canon[n]: {"qid": None, "titles": {}} for n in batch
            }
            for b in bindings:
                num = b["chebi"]["value"]
                cid = num_to_canon.get(num)
                if cid is None:
                    continue
                qid_uri = b["qid"]["value"]
                article_url = b["article"]["value"]
                lang = b["lang"]["value"]
                rec = batch_results[cid]
                rec["qid"] = _qid_short(qid_uri)
                title = _title_from_url(article_url)
                if lang not in rec["titles"]:
                    rec["titles"][lang] = title
            cache.update(batch_results)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
            time.sleep(SLEEP_BETWEEN_BATCHES)

    return {c: cache[c] for c in chebi_ids if c in cache}
