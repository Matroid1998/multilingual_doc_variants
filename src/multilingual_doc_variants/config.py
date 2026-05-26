"""Project-wide configuration: paths, language list, model id, RNG seed."""
from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

CORPUS_CSV = DATA_DIR / "google_patents" / "multilingual_corpus.csv"

KG_DIR = DATA_DIR / "kg"
CHEBI_RAW_DIR = KG_DIR / "chebi_raw"
CHEBI_CONCEPTS_PARQUET = KG_DIR / "chebi_concepts.parquet"
WIKIDATA_CACHE_JSON = KG_DIR / "wikidata_cache.json"
CHEBI_RELEASE_METADATA = CHEBI_RAW_DIR / "release_metadata.json"

CORPUS_LINKED_PARQUET = DATA_DIR / "corpus" / "documents_linked.parquet"

IDEA1_DIR = DATA_DIR / "idea1"
IDEA1_INSTANCES_JSONL = IDEA1_DIR / "instances.jsonl"
IDEA1_RUN_SUMMARY = IDEA1_DIR / "run_summary.json"

IDEA2_DIR = DATA_DIR / "idea2"
IDEA2_INSTANCES_JSONL = IDEA2_DIR / "instances.jsonl"
IDEA2_LLM_CACHE_JSONL = IDEA2_DIR / "llm_cache.jsonl"
IDEA2_RUN_SUMMARY = IDEA2_DIR / "run_summary.json"

LANGS = ("en", "de", "es", "fr", "zh")

RNG_SEED = 42

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Benchmark sizing
BENCH1_TARGET_COUNT = 80          # mid of 50-100
BENCH2_TARGET_COUNT = 80
HARD_NEG_PER_GOLD = 10

# Stage 2 tuning
MIN_ALIAS_LEN = 3                 # Latin-script aliases
MIN_ALIAS_LEN_ZH = 2

# Stage 4 tuning
MIN_TOKENS_FOR_SOURCE_ROW = 200
