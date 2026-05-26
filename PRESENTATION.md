# Multilingual Chemistry Data Creation Pipeline

> Source file for Claude-on-PowerPoint. Each top-level `##` heading is one slide. Keep slide content concise; the bullets below each heading are the slide bullets. Speaker notes (when present) are in `> ` blockquotes and should go in PowerPoint's notes pane, not on the slide.

---

## Slide 1 — Title

**Multilingual Chemistry Data Creation Pipeline**

A 4-stage pipeline that turns a raw multilingual patent corpus into two retrieval benchmarks for chemistry concepts.

- 1,110 patent documents · 5 languages (EN / DE / ES / FR / ZH)
- Built on ChEBI (EMBL-EBI's chemistry ontology)
- LLM-assisted for the code-switched benchmark

> Speaker notes: introduce the problem briefly — there's no good multilingual chemistry retrieval benchmark, and patent text is hard because chemistry terms are domain-specific and often code-switched. We build the data; downstream pipelines build retrievers + measure metrics.

---

## Slide 2 — What we're building

Two retrieval benchmarks, each ~50–100 instances:

- **Idea 1 — Alias-Graph Data**: query is a multilingual alias set, must retrieve the right document and reject confusable-neighbor documents.
- **Idea 2 — Code-Switched Document Data**: real patents with exactly one chemistry term swapped into another language. Tests robustness to code-switching.

No queries, no retrievers, no metrics built here — those are downstream.

---

## Slide 3 — The pipeline at a glance

Four stages, three artifacts plus an LLM cache:

```
Stage 1: ChEBI download + parse  →  data/kg/chebi_concepts.parquet
Stage 2: Entity linking          →  data/corpus/documents_linked.parquet
Stage 3: Idea 1 assembly         →  data/idea1/instances.jsonl
Stage 4: Idea 2 (LLM-driven)     →  data/idea2/instances.jsonl  +  llm_cache.jsonl
```

> Speaker notes: stages 1–3 are fully deterministic. Stage 4 is the only place that talks to the LLM API. Reruns are cached.

---

## Slide 4 — Stage 1: the ChEBI knowledge slice

Download ChEBI flat files from EMBL-EBI (~360 MB):

- `chebi.obo` — ontology with `is_a`, `has_role`, etc.
- `names.tsv.gz` — multilingual synonyms keyed by ChEBI ID
- `compounds.tsv.gz` · `structures.tsv.gz` · `chemical_data.tsv.gz` · `relation.tsv.gz`

For every ChEBI concept mentioned in the corpus, extract:

- Identity (`chebi_id`, `inchikey`, `smiles`, `formula`)
- **Multilingual aliases** (`en/de/es/fr/zh`)
- **Neighbor closures**: `siblings_isa`, `siblings_role`, `conjugate_pair`, `tautomers`, stereo/tautomer InChIKey block, parent_hydride_family

Result: **1,003 corpus-relevant concepts** with full neighbor lists.

---

## Slide 5 — Stage 2: monolingual entity linking, cross-lingual by virtue of ChEBI

**No machine translation. No cross-language alignment.** Five independent monolingual scans.

The bridge is one file in ChEBI: `names.tsv.gz`. For "catalyst" (CHEBI:35223):

| COMPOUND_ID | NAME         | LANGUAGE |
|---|---|---|
| 35223 | catalyst     | en |
| 35223 | catalyseur   | fr |
| 35223 | Katalysator  | de |
| 35223 | catalizador  | es |

- Five per-language Aho-Corasick automata (built from these rows).
- Word-boundary matching, case folding, longest-match dedup.
- Same `chebi_id` falls out for free because ChEBI curators populated it.

Result: **9,634 mentions in 794/1,110 documents**.

> Speaker notes: this is the key conceptual point. We get cross-lingual retrieval for free because ChEBI is a shared identity layer. For a non-chemistry domain we'd need MT or a multilingual entity model.

---

## Slide 6 — Idea 1: Alias-Graph Data

**Question:** given a multilingual alias set, can a retriever find the right document across languages while ignoring confusable neighbors?

**Concept qualifies for inclusion if:**

- Mentioned in the corpus in ≥ 2 languages
- Has aliases in ≥ 3 of the 5 target languages
- Has ≥ 1 confusable neighbor that also appears in the corpus
- Prefer ChEBI 3-star (manually curated)

**Each instance carries:**

- One **gold document** (single language, sampled from where the concept appears)
- Up to 10 **hard-negative documents** mentioning a confusable neighbor, in languages **different from the gold** (cross-lingual by construction)
- Neighbor concept metadata (which relation made them confusable)

---

## Slide 7 — Idea 1 example: CHEBI:27007 (tin)

**Multilingual aliases (the query side):**

| lang | alias |
|---|---|
| en | tin, Sn |
| de | Zinn |
| es | estaño |
| fr | étain |

**Gold document:** `EP-4634425-A1_en` (English) — patent on non-oriented electrical steel: `...0% ≤ Tin ≤ 0.2%...`

**Hard negatives (must be ≠ English):**

| neg doc | lang | neighbor | relation |
|---|---|---|---|
| `EP-4634414-A1_fr` | fr | CHEBI:18248 iron | siblings_role |
| `EP-4630589-A1_de` | de | CHEBI:18248 iron | siblings_role |
| `WO-2025209612-A1_es` | es | CHEBI:27363 zinc | siblings_isa |
| `WO-2025210044-A1_de` | de | CHEBI:27594 carbon | siblings_isa |

> Speaker notes: a naive lexical retriever sees "tin/Zinn/étain/estaño" and may surface the iron/zinc/carbon documents instead — that's exactly the failure mode this benchmark exposes.

---

## Slide 8 — Idea 1 results

74 instances written from 90 qualifying concepts (6 dropped — no cross-lingual neg pool).

**Gold-document languages** (well-distributed):

| en | fr | de | es |
|---|---|---|---|
| 35 | 36 | 5 | 4 |

**Hard-negative documents by language:** en 229, fr 178, de 161, es 43 (a total of 611 negs across 74 instances).

**Confusability sources:** siblings_role 304 · siblings_isa 302 · stereo/tautomer 4 · conjugate_pair 1.

---

## Slide 9 — Idea 2: Code-Switched Document Data

Take a real patent. Swap **exactly one chemistry term** into another language. Five variant types:

| tag | what happens |
|---|---|
| `A_clean` | original text, unchanged (baseline) |
| `B_in_set` | swap to a language already in the patent's translation set |
| `C_out_of_set` | swap to a language NOT in the translation set |
| `D_noisy` | swap with an orthographically perturbed version of the same term (typos, hyphens, case noise) |
| `E_control_nonchem` | swap a **non-chemistry** noun — control variant |

Three positions for B/C: `title`, `first_sentence`, `body`.
Up to **9 records per source row**.

---

## Slide 10 — Substitution mechanics (the key design choice)

Two-step process, exactly as the spec calls for:

1. **Pipeline picks** the term to remove and exactly where.
2. **LLM generates** the substitute term (B/C/D/E), with the whole document as context.
3. **Pipeline splices**: `text[:start] + swap_term + text[end:]` — exactly one occurrence replaced.
4. **LLM verifies**: semantic preservation + round-trip translation.

```
+-------------+    +-----------+    +-----------+    +-----------+
| pick term   | -> | LLM:      | -> | splice    | -> | LLM:      |
| (offset,    |    | generate  |    | det.      |    | verify    |
| chebi_id)   |    | swap term |    | substitute|    | semantics |
+-------------+    +-----------+    +-----------+    +-----------+
```

> Speaker notes: we tried letting the LLM rewrite the whole document but it kept either replacing every occurrence of the source term or generating empty completions on GPT-5. Term-only generation + deterministic splicing is much more reliable and cheaper.

---

## Slide 11 — Idea 2 example: patent EP-4634109-A1_fr

Source: French patent. `L_avail = {en, fr}`. Eight records, all 5 variant types fired.

| variant | position | original → swap | swap lang |
|---|---|---|---|
| A_clean | na | — | — |
| B_in_set | title | hydrogène → hydrogen | en (in-set) |
| B_in_set | first_sentence | hydrogène → hydrogen | en |
| B_in_set | body | hydrogène → hydrogen | en |
| C_out_of_set | title | hydrogène → hidrógeno | es (out-of-set) |
| C_out_of_set | first_sentence | hydrogène → hidrógeno | es |
| D_noisy | body | hydrogène → h-y-drogène | fr (same lang) |
| E_control | body | ligne → line | en (non-chem noun) |

> Speaker notes: same chemistry term ("hydrogène") translated to two different languages, perturbed in source language, plus a non-chemistry control. The deterministic mention picker chose the same offset; only the LLM-generated swap term changed.

---

## Slide 12 — Why LLM-generated swap terms, not lookup tables?

**For B (in-set):** we used to read the term from the parallel-language patent row. Works only when ChEBI / the patent both contain the surface form.

**For C (out-of-set):** we used to read from `aliases_chebi[swap_lang][0]`. Fails when ChEBI has no synonym for that language — especially **Chinese (0 native synonyms across all of ChEBI for many concepts)**.

**For D (noisy):** we used a rule-based catalog (hyphen, case, typo). Limited to a fixed perturbation set; couldn't adapt to specific terms.

**LLM-generated approach** unifies all four:

- One prompt per variant, full doc as context, variant-specific rule clause.
- LLM returns ONE LINE — just the substitute term.
- Pipeline does the deterministic splice (no LLM rewriting the document).

---

## Slide 13 — Stage 4 pipeline architecture

```
Source row  ----+
                |   +---------------+
position +------+-->|  mention      |
ranges          |   |  picker       |--+
                |   +---------------+  |  (term, offset)
mentions  ------+                      |
                                       v
              +---------------------------------+
              | LLMClient.generate_swap_term()  |
              | system prompt + doc + rule      |
              +---------------------------------+
                                       |
                                       v   swap_term (one line)
              +---------------------------------+
              | deterministic splice            |
              | text[:start] + swap + text[end:]|
              +---------------------------------+
                                       |
                                       v
                       +----------------+
                       | verify:        |
                       |  - presence    |
                       |  - semantics   |  -> instance record
                       |  - round-trip  |
                       +----------------+
```

---

## Slide 14 — Reproducibility & cost

- **Fixed RNG seed** (42) for every sampling step.
- **ChEBI release pinned** via HTTP Last-Modified headers in `data/kg/chebi_raw/release_metadata.json`.
- **LLM calls content-hashed and cached** to `data/idea2/llm_cache.jsonl` — second runs cost $0.
- **Stages 1–3** complete end-to-end in **under 15 seconds** on cached ChEBI.
- **Stage 4** with 50 source rows on gpt-4o-mini: **8.7 minutes, 666 cached calls, well under $1**.
- 11 pytest smoke tests cover full-text concatenation, longest-match dedup, cache key stability, and per-variant prompt sanity.

---

## Slide 15 — Final artifacts & CLI

| File | Contents |
|---|---|
| `data/kg/chebi_concepts.parquet` | 1,003 ChEBI concepts + neighbors |
| `data/corpus/documents_linked.parquet` | 1,110 docs, 9,634 mentions |
| `data/idea1/instances.jsonl` | 74 Benchmark-1 instances (cross-lingual gold/neg) |
| `data/idea2/instances.jsonl` | **201 Benchmark-2 records** from 50 source rows |
| `data/idea2/llm_cache.jsonl` | 666 cached LLM calls (term generation + verification) |

**Command-line interface:**

```bash
uv run mdv stage1                      # build the KG slice
uv run mdv stage2                      # entity linking
uv run mdv stage3                      # build Benchmark 1
uv run mdv stage4 --limit 50           # build Benchmark 2 (LLM-driven)
uv run mdv all                         # stages 1 -> 4
```

---

## Slide 16 — Known limits + next steps

**Current:**

- Chinese alias coverage in the KG is near-zero (ChEBI has almost no native ZH synonyms). LLM term generation in stage 4 compensates; stage 2 linking does not.
- Wikidata Wikipedia-title crosswalk is gated behind `--with-wikidata` (the WDQS endpoint kept rate-limiting us during initial testing).

**Next steps:**

- Run `--with-wikidata` once the WDQS soft-ban clears to enrich aliases for ZH/ES.
- Scale stage 4 to 100 source rows (corpus has 498 qualifying source rows; we sampled 50).
- Add additional verification: e.g., explicit chemistry-equivalence check between original and swap term using ChEBI structural data.

---

## Slide 17 — Thank you

Questions?

- Code: `src/multilingual_doc_variants/` (≈ 1,500 LoC)
- Spec: `chem_bench_spec.md`
- Full walk-through: `HOW_IT_WORKS.md`
