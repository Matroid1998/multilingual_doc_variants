# Code Flow — `multilingual_doc_variants`

A four-stage, mostly-deterministic data-creation pipeline. Each stage reads the
artifact(s) the previous stage wrote and appends its own. Only stage 4 touches an
LLM.

---

## 1. Top-level pipeline (artifacts as the spine)

```mermaid
flowchart TD
    CLI["cli.py — typer app<br/>mdv stage1 | stage2 | stage3 | stage4 | all"]

    CLI --> S1
    CLI --> S2
    CLI --> S3
    CLI --> S4

    subgraph S1 ["Stage 1 — KG slice (stage1_kg/build.py)"]
        direction TB
        DL["download_chebi()"] --> PARSE["load_chebi() → ChebiData<br/>NeighborIndex"]
        PARSE --> BOOT["bootstrap aliases<br/>(ChEBI synonyms only)"]
        BOOT --> LINK1["_find_mentioned_ids()<br/>reuses Stage-2 linker"]
        LINK1 --> WD{"--with-wikidata?"}
        WD -- yes --> XW["crosswalk() → Wikipedia titles"]
        WD -- no --> ROW
        XW --> ROW["_row_for_concept() per id"]
    end

    subgraph S2 ["Stage 2 — entity linking (stage2_link/build.py)"]
        direction TB
        D2["build_dictionaries()<br/>per-lang Aho-Corasick"] --> L2["link_document() per corpus row"]
        L2 --> P2["primary_chebi_id = most common"]
    end

    subgraph S3 ["Stage 3 — Benchmark 1 (stage3_idea1/build.py)"]
        direction TB
        Q3["qualifying_concepts()<br/>hard filters"] --> SMP3["balanced_sample()<br/>stratified, star=3 pref"]
        SMP3 --> GN["per concept:<br/>pick_gold_document()<br/>hard_negatives()"]
    end

    subgraph S4 ["Stage 4 — Benchmark 2 (stage4_idea2/build.py)"]
        direction TB
        Q4["qualifying_source_rows()<br/>≥1 mention, ≥200 tokens"] --> SMP4["sample_source_rows()<br/>1 per publication_number"]
        SMP4 --> EMIT["per row emit A / B×3 / C×3 / D / E"]
    end

    RAW[("data/kg/chebi_raw/*")]
    KG[("chebi_concepts.parquet")]
    LINKED[("documents_linked.parquet")]
    CSV[("multilingual_corpus.csv")]
    I1[("idea1/instances.jsonl")]
    I2[("idea2/instances.jsonl")]

    DL --> RAW --> PARSE
    CSV --> LINK1
    ROW --> KG
    KG --> D2
    CSV --> L2
    P2 --> LINKED
    KG --> Q3
    LINKED --> Q3
    GN --> I1
    KG --> Q4
    LINKED --> Q4
    CSV --> EMIT
    EMIT --> I2
```

---

## 2. Shared foundation (used by every stage)

```mermaid
flowchart LR
    CFG["config.py<br/>paths · LANGS · RNG_SEED · OPENAI_MODEL · thresholds"]
    CORP["corpus.py<br/>load_rows() → CorpusRow{title…context, text}<br/>compute_l_avail() → {pub#: {langs}}"]
    IO["io_utils.py<br/>nfc() · fold() · write/append_jsonl()"]

    CFG --- CORP
    CFG --- IO
    CORP -.->|"text = title\nabstract\ndescription\nfirst_claim\ncontext"| OFFSETS["all char offsets<br/>are relative to this join"]
```

The concatenation order in `corpus.py` (`TEXT_FIELDS`) defines the coordinate
system: every `start`/`end` produced in stage 2 and every position range in
stage 4 is an index into this single joined string.

---

## 3. Stage 2 linker — the engine reused by Stages 1, 3 and 4

```mermaid
flowchart TD
    A["aliases_combined<br/>{chebi_id: {lang: [surface forms]}}"] --> B["build_dictionaries()"]
    B --> C["LangDict per lang<br/>Aho-Corasick automaton + inverted map"]
    C --> D["link_document(text, lang)"]

    D --> E["NFC + casefold (skip zh)<br/>track idx_map back to original"]
    E --> F["Aho-Corasick .iter() → raw matches"]
    F --> G["word-boundary filter (Latin only)"]
    G --> H["longest match per start"]
    H --> I["left→right non-overlapping sweep"]
    I --> J["_resolve_ambiguous()<br/>tie-break by co-occurring aliases"]
    J --> K["mentions[] {surface, start, end, chebi_id}"]
```

One concept gets the same `chebi_id` across languages **only because ChEBI's
`names.tsv.gz` already maps `catalyst`/`catalyseur`/`Katalysator` to the same
compound id.** There is no translation or alignment step — each language is a
fully independent monolingual scan.

---

## 4. Stage 4 inner loop — the only LLM path

```mermaid
flowchart TD
    SR["SourceCandidate row"] --> POS["compute_position_ranges()<br/>title / first_sentence / body"]
    POS --> PM["pick_mention()<br/>filter by position + corpus_freq≥3<br/>(+ parallel-row req for B)<br/>drop prefs → fallback_flag"]
    PM --> GEN["client.generate_swap_term()<br/>LLM, content-hashed cache"]

    GEN --> SPL["_deterministic_substitute()<br/>text[:s] + swap_term + text[e:]"]
    SPL --> V1{"term appears<br/>exactly once?"}
    V1 -- no --> REJ1["reject: term_presence"]
    V1 -- yes --> V2{"semantic_preserved()<br/>LLM yes/no"}
    V2 -- no --> REJ2["reject: semantic_preservation"]
    V2 -- yes --> V3["round_trip() LLM<br/>(B/C/E only)<br/>sets round_trip_flag"]
    V3 --> OUT["append record → instances.jsonl"]

    subgraph VARIANTS ["swap-term source per variant"]
        VB["B in-set → parallel-row form / KG alias in L_avail"]
        VC["C out-of-set → KG alias outside L_avail"]
        VD["D noisy → perturb same-language surface (no round-trip)"]
        VE["E control → spaCy noun NOT overlapping a mention"]
    end
    VARIANTS -.-> GEN
```

LLM calls go through `LLMClient` → `LLMCache` (SHA-256 of the request payload,
appended to `idea2/llm_cache.jsonl`). Re-running stage 4 on the same rows is a
cache hit on every call → `$0` and deterministic.
