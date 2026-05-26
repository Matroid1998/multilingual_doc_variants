# multilingual_doc_variants

Four-stage data-creation pipeline producing document-level annotations and variants from a multilingual patent corpus for two future chemistry retrieval benchmarks. See [chem_bench_spec.md](chem_bench_spec.md) for the full specification.

## Quickstart

```bash
uv sync
uv run python -m spacy download xx_ent_wiki_sm   # multilingual POS tagger for variant E
export OPENAI_API_KEY=...                         # only needed for stage 4
export OPENAI_MODEL=gpt-5                         # configurable; default gpt-5

uv run mdv stage1      # ChEBI download + KG slice -> data/kg/chebi_concepts.parquet
uv run mdv stage2      # entity linking            -> data/corpus/documents_linked.parquet
uv run mdv stage3      # Benchmark 1 instances     -> data/idea1/instances.jsonl
uv run mdv stage4      # Benchmark 2 variants      -> data/idea2/instances.jsonl
# or
uv run mdv all
```

## Layout

```
src/multilingual_doc_variants/
  stage1_kg/      ChEBI flat-file + Wikidata SPARQL crosswalk
  stage2_link/    Per-language Aho-Corasick longest-match linker
  stage3_idea1/   Concept qualification + hard-negative mining
  stage4_idea2/   Source-row sampling + LLM-driven term substitution
```
