# Multilingual Chemistry Data Creation — Task Spec

## 1. Scope

The task is to build a four-stage data-creation pipeline that produces document-level annotations and variants from an existing multilingual patent corpus, for two future retrieval benchmarks. Target output volume is 50 to 100 instances per benchmark. No queries are generated, no retrievers are built, no metrics are computed; downstream pipelines handle those.

The pipeline operates at full-document granularity. The corpus is not split into passages or chunks.

The pipeline produces three artifacts:

- A ChEBI-derived chemistry concept table covering EN/DE/ES/FR/ZH, augmented with multilingual Wikipedia article titles fetched through a Wikidata crosswalk.
- An entity-linked, hard-negative-annotated set of corpus instances for Benchmark 1 ("Alias-Graph Data").
- A set of document variants for Benchmark 2 ("Code-Switched Document Data"), with one chemistry term per variant substituted into another language, plus orthographic-noise variants and non-chemistry control variants.

The substituting LLM for Benchmark 2 is OpenAI GPT-5.5.

---

## 2. Inputs

### 2.1 The corpus

The corpus lives at `data/google_patents/multilingual_corpus.csv` with the following columns:

| column | meaning | use |
|---|---|---|
| `id` | unique row identifier of the form `{publication_number}_{lang}`, for example `EP-4626234-A1_de` | unique document identifier across the pipeline |
| `language` | ISO-639-1 code: one of `en`, `de`, `es`, `fr`, `zh` | routing to language-specific NER and alias dictionaries |
| `title` | document title in that language | useful site for term swaps |
| `abstract` | short summary | high entity density |
| `description` | main body text | primary site for chemistry mentions |
| `first_claim` | first legal claim of the patent | high chemistry-term density |
| `context` | additional contextual text | additional text |
| `publication_number` | patent identifier without the language suffix, for example `EP-4626234-A1` | **parallel-group identifier**: rows with the same `publication_number` but different `language` are translations of each other |
| `country_code` | issuing patent office | metadata only |
| `publication_date` | date of publication | metadata only |
| `source` | provenance tag | metadata only |

Derived quantities the pipeline must compute:

- For each `publication_number` `p`, `L_avail(p)` is the set of languages for which a row exists with that `publication_number`. For example, if rows `EP-4626234-A1_de` and `EP-4626234-A1_fr` are both present, `L_avail("EP-4626234-A1") = {de, fr}`.
- The full document text of a row is the concatenation of `title`, `abstract`, `description`, `first_claim`, and `context`, joined by single newlines in that order. Character offsets used by mention annotations are relative to this concatenated full document text.

### 2.2 The knowledge graph: ChEBI, with Wikipedia langlinks crosswalk

The canonical chemistry knowledge graph is **ChEBI** (Chemical Entities of Biological Interest, maintained by EMBL-EBI, CC-BY 4.0). ChEBI provides every concept's canonical identity, its English label, its native multilingual synonyms (where present), its ontological relations, and its molecular structure. Confusable-neighbor relations for hard-negative mining are derived entirely from the ChEBI ontology and ChEBI's InChI fields. Multilingual coverage is augmented — but only for surface labels — by fetching Wikipedia article titles in DE, ES, FR, and ZH through a thin Wikidata crosswalk.

**ChEBI data sources** (FTP at `https://ftp.ebi.ac.uk/pub/databases/chebi/Flat_file_tab_delimited/` and `https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/`):

- `chebi.obo` (or `chebi.owl`) — the ontology with `is_a`, `has_role`, `has_part`, `has_parent_hydride`, `is_conjugate_acid_of`, `is_conjugate_base_of`, `is_tautomer_of` relations.
- `names.tsv.gz` — synonyms with columns `ID`, `COMPOUND_ID`, `TYPE`, `SOURCE`, `NAME`, `ADAPTED`, `LANGUAGE`. The `LANGUAGE` column is the source of native multilingual labels.
- `compounds.tsv.gz` — primary compound table with columns including `ID`, `NAME`, `DEFINITION`, `STAR` (curation level), `STATUS`, `PARENT_ID`.
- `chemical_data.tsv.gz` — formulas, charges, masses.
- `structures.csv.gz` — SMILES and InChI per ChEBI ID, with `TYPE` and `DIMENSION` fields.
- `relation.tsv` — relations table with `TYPE`, `INIT_ID`, `FINAL_ID`.

The `libChEBI` Python library is an acceptable convenience for programmatic access; raw flat-file parsing is equally acceptable. Pin a single ChEBI release date and record it in the run metadata.

**Wikidata crosswalk** (used only as a label-augmentation source, not as a graph). The Wikidata SPARQL endpoint at `https://query.wikidata.org/sparql` is queried in batches for QIDs that carry a ChEBI ID through property `wdt:P683`. For each matched QID, the Wikipedia sitelinks for the EN, DE, ES, FR, and ZH wikis are read. The resulting article titles serve as additional surface forms for the concept in their respective languages. No structural information (parents, classes, neighbors) is taken from Wikidata or Wikipedia; structure stays in ChEBI.

**Stage output: the cached KG slice.** A single file `data/kg/chebi_concepts.parquet` containing one row per chemistry concept relevant to the selected instances. Schema:

| field | meaning |
|---|---|
| `chebi_id` | the ChEBI accession, for example `CHEBI:15365`. This is the canonical concept identity used everywhere downstream |
| `chebi_name_en` | the primary ChEBI name |
| `inchikey` | InChIKey from the `structures.csv.gz` file |
| `inchi` | full InChI |
| `smiles` | SMILES |
| `formula` | molecular formula |
| `chebi_star` | ChEBI curation level (1, 2, or 3) |
| `aliases_chebi` | mapping from language code to list of native ChEBI synonyms in that language, sourced from `names.tsv.gz` filtered by `LANGUAGE` |
| `wikidata_qid` | the Wikidata QID for this ChEBI ID, or null if no crosswalk match |
| `wikipedia_titles` | mapping from language code to Wikipedia article title in that language, sourced from sitelinks of the matched QID |
| `aliases_combined` | mapping from language code to the deduplicated union of `aliases_chebi` and `wikipedia_titles` for that language |
| `parents_isa` | list of ChEBI IDs that are immediate `is_a` parents of this concept |
| `roles` | list of ChEBI IDs that this concept `has_role` |
| `siblings_isa` | list of ChEBI IDs that share at least one immediate `is_a` parent with this concept; deduplicated |
| `siblings_role` | list of ChEBI IDs that share at least one `has_role` value with this concept; deduplicated |
| `conjugate_pair` | list of ChEBI IDs related to this concept by `is_conjugate_acid_of` or `is_conjugate_base_of` |
| `tautomers` | list of ChEBI IDs related by `is_tautomer_of` |
| `stereo_or_tautomer_inchikey_block` | list of ChEBI IDs whose InChIKey first 14 characters match this one's, excluding this concept itself; covers stereoisomers and tautomers not encoded explicitly |
| `parent_hydride_family` | list of ChEBI IDs sharing the same `has_parent_hydride` value |

For the POC, the KG slice does not need to be populated for every ChEBI entry. It only needs to cover the concepts actually selected for the 50–100 instances in stages 3 and 4, plus all their neighbors. A practical approach is to build the slice on demand: identify candidate concepts from corpus mentions, then crawl those concepts and their neighbor closures.

---

## 3. Benchmark 1 — Alias-Graph Data

### 3.1 What is produced

For 50 to 100 ChEBI concepts, produce `data/idea1/instances.jsonl` where each line is one instance with the following fields:

| field | meaning |
|---|---|
| `chebi_id` | the ChEBI concept identity |
| `inchikey` | for downstream joins |
| `multilingual_aliases` | mapping from language code to the `aliases_combined` list for that language; copied from the KG slice |
| `gold_documents` | list of corpus documents that mention this concept, each with full document text, source `id`, `publication_number`, `language`, and the character offsets of every mention of this concept within the document |
| `hard_negative_documents` | list of corpus documents that mention confusable neighbors of this concept (see section 3.3) |
| `neighbor_concepts` | for each unique neighbor `chebi_id` appearing in `hard_negative_documents`, its primary English name, its `aliases_combined`, and the relation tag that made it a neighbor |

### 3.2 How concepts and their gold documents are selected

Entity linking is performed against the corpus first, then concepts are filtered for inclusion.

**Entity linking.** Build a per-language surface-form dictionary by inverting the `aliases_combined` field of the KG slice. Perform longest-match dictionary lookup over each corpus document's full concatenated text with Unicode NFC normalization and case folding (case folding is skipped for ZH). Ambiguous surface forms — one form mapping to multiple `chebi_id`s — are kept; disambiguate by selecting the `chebi_id` whose other aliases also appear elsewhere in the same document. A chemistry NER pass on top of dictionary lookup may be added as a recall booster but is not required.

**Concept selection.** A ChEBI concept qualifies for inclusion as a Benchmark 1 instance when all of the following hold:

- It has corpus documents that mention it in at least two distinct languages.
- It has aliases in at least three of the five target languages (counting both ChEBI native synonyms and Wikipedia titles).
- It has at least one confusable neighbor (defined in section 3.3) that also appears somewhere in the corpus.
- Its ChEBI curation level (`chebi_star`) is 3 — manually curated — where possible. If insufficient three-star concepts qualify, allow two-star.

Sample 50 to 100 qualifying concepts uniformly, with two soft preferences: a balance across alias-language counts (do not let extremely popular concepts dominate), and a balance across coarse concept types where detectable from ChEBI roles (small molecule, drug, polymer, salt, biochemical).

### 3.3 How hard-negative documents are mined

For each selected `chebi_id` `g`, the candidate neighbor set is the union of these fields from the KG slice:

- `siblings_isa[g]` — concepts sharing an immediate `is_a` parent with `g`, the chemistry-class confusion case.
- `siblings_role[g]` — concepts sharing a `has_role` value with `g`, the same-functional-class confusion case.
- `conjugate_pair[g]` — conjugate acid–base pairs.
- `tautomers[g]` and `stereo_or_tautomer_inchikey_block[g]` — stereoisomers and tautomers.
- `parent_hydride_family[g]` — concepts derived from the same parent hydride.

For each neighbor concept `n`, collect corpus documents that mention `n` but do not mention `g`. From this pool, retain up to 10 hard-negative documents per gold concept, sampled to cover multiple neighbor concepts when possible and to span at least two languages where the data allows. Each retained document is written with the full document text, source `id`, `publication_number`, `language`, character offsets of the neighbor's mentions within the document, the neighbor `chebi_id` that triggered selection, and the relation tag (`siblings_isa`, `siblings_role`, `conjugate_pair`, `tautomers`, `stereo_or_tautomer_inchikey_block`, or `parent_hydride_family`).

### 3.4 Intermediate artifact

Stage 3 emits one intermediate file consumed both by section 3.3 and by stage 4: `data/corpus/documents_linked.parquet`. One row per corpus document (one row per CSV row):

| field | meaning |
|---|---|
| `id` | the original CSV `id` |
| `publication_number` | parallel-group identifier |
| `language` | document language |
| `text` | the full concatenated document text |
| `mentions` | list of detected mentions in the full text, each with surface form, character offsets relative to `text`, and resolved `chebi_id` |
| `primary_chebi_id` | the most frequently mentioned `chebi_id` in the document, or null if no mentions |

---

## 4. Benchmark 2 — Code-Switched Document Data

### 4.1 What is produced

For 50 to 100 source corpus rows, produce `data/idea2/instances.jsonl`. Each source row yields multiple records, one per variant. Variant types:

| variant tag | description | position dimension |
|---|---|---|
| `A_clean` | the source row's document text, unchanged | not applicable |
| `B_in_set` | one chemistry term substituted to a language in `L_avail(p) \ {L_src}` | one of `title`, `first_sentence`, `body` |
| `C_out_of_set` | one chemistry term substituted to a language in `{en, de, es, fr, zh} \ L_avail(p)` | one of `title`, `first_sentence`, `body` |
| `D_noisy` | rule-based orthographic perturbations applied to one chemistry term in the source language | `body` |
| `E_control_nonchem` | one non-chemistry noun substituted into another language, in either in-set or out-of-set style | `body` |

A source row whose `L_avail` is a strict subset of the five target languages produces both B and C variants. A row whose `L_avail` covers all five languages produces B variants only. A monolingual row (`|L_avail| = 1`) produces C variants only. Variants A, D, and E are produced for every source row regardless of `L_avail`.

For each applicable variant–position combination, one record is produced. Fan-out is up to nine records per source row (A, B at three positions, C at three positions, D, E).

### 4.2 Source row selection

A corpus row qualifies as a source row when it has at least one chemistry mention resolvable to a ChEBI concept and its concatenated document text contains at least 200 tokens. Sample 50 to 100 qualifying rows uniformly with a soft preference for `|L_avail(p)|` of 2 or 3. Do not sample more than one row per `publication_number`.

### 4.3 Substitution mechanics

Every non-trivial variant is a two-step process. The pipeline first deterministically chooses which surface form to remove and which target term to insert. OpenAI GPT-5.5 then performs the fluent edit.

**Variant B (in-set code-switch).** The target swap language `L_swap` is sampled uniformly from `L_avail(p) \ {L_src}`. The term to remove is a chemistry mention from the source row's document, selected per section 4.4. The target term is the surface form as it appears in the parallel row for the same `publication_number` in language `L_swap`. Where multiple surface forms occur in the parallel row for the same concept, prefer the one matching the ChEBI primary name in `L_swap` from `aliases_chebi`, with `wikipedia_titles[L_swap]` as a tie-breaker. If the concept has no mention in the parallel row, fall back to the KG-derived alias (preferring `aliases_chebi[L_swap]` first entry, otherwise `wikipedia_titles[L_swap]`) and set `fallback_flag = true` on the output record.

**Variant C (out-of-set code-switch).** `L_swap` is sampled uniformly from `{en, de, es, fr, zh} \ L_avail(p)`. The term to remove is a chemistry mention from the source row's document. The target term comes from the KG slice: prefer the first entry of `aliases_chebi[L_swap]`; if empty, use `wikipedia_titles[L_swap]`.

**Variant D (noisy).** The term to remove is a chemistry mention from the source row's document. A rule-based perturbation is applied to the mention, sampled from this catalog: hyphenation insertion or removal (`amlo-dipine` ↔ `amlodipine`); Greek-letter substitution (`α` ↔ `alpha` ↔ `a`); oxidation-state notation swaps (`Fe(III)` ↔ `Fe3+` ↔ `iron(3+)`); locant whitespace drift (`1,2-dichloroethane` → `1 2 dichloroethane`); subscript or superscript loss (`H₂O` → `H2O`); light typos at one in twenty characters; case noise. One or two perturbations per term are applied. The perturbed form replaces the original surface form in place. No LLM call is needed for variant D.

**Variant E (non-chemistry control).** A non-chemistry common noun is selected from the source document. Operationalize by running a general-purpose multilingual POS tagger and picking a noun that is not part of any chemistry mention. The target language is sampled with equal probability from `L_avail(p) \ {L_src}` (in-set style) and `{en, de, es, fr, zh} \ L_avail(p)` (out-of-set style); the chosen style is recorded in the output. For in-set style, harvest the equivalent noun from the parallel row by aligning on sentence context. For out-of-set style, translate the noun via GPT-5.5 against a small fixed vocabulary cached at pipeline start. The same fluency-preserving LLM-driven substitution as B and C is then applied.

**Position dimension for B and C.** Three positions are supported:

- `title` — the mention to be swapped must lie inside the row's `title` field.
- `first_sentence` — the mention must lie inside the first sentence of the `abstract` or `description`, whichever is non-empty first.
- `body` — the mention lies in `description`, `first_claim`, or `context` past the first sentence.

If no chemistry mention is available in a requested position for a given source row, that position–variant combination is skipped and recorded in the run summary as a skip.

### 4.4 Which chemistry mention to substitute

Within a chosen position, select exactly one chemistry mention from the source row's document with these soft preferences in order:

- Prefer mentions whose resolved `chebi_id` has at least three corpus-wide mentions across all languages.
- Prefer body mentions for the `body` position, title mentions for `title`, and first-sentence mentions for `first_sentence`.
- For variant B specifically, additionally require that the concept appears as a mention somewhere in the parallel row in `L_swap` — the operational definition of "the term is widely used in that translation". If no chemistry mention satisfies all soft preferences, drop them one at a time in this order: first the corpus-frequency floor, then the parallel-mention requirement for B (in which case `fallback_flag = true`).

### 4.5 The LLM's role

OpenAI GPT-5.5 is invoked once per non-trivial variant (B, C, and E). The prompt provides the source-language text, the source surface form and its character offsets, and the target-language term to insert. The model is instructed to make only minimal grammatical adjustments needed to keep the sentence well-formed (article agreement, declension, light reordering), keep the inserted term unchanged in its swap-language form, and leave all other text untouched. The deterministic choice of which mention to remove and which term to insert is made by the pipeline before the LLM is invoked; the LLM is responsible only for fluent rewriting.

Every LLM call is content-hashed by prompt, model identifier, and decoding parameters, and persisted to `data/idea2/llm_cache.jsonl`. Reruns must consult the cache before issuing any new call.

### 4.6 Verification of each substitution

Every LLM rewrite passes the following checks before being written to the output:

- **Term-presence check.** The target term must appear exactly once in the rewrite under Unicode NFC normalization. Zero or multiple occurrences cause rejection.
- **Source-term-absence check.** The original surface form must not appear at the swap site in the rewrite. The form may legitimately appear elsewhere if it is also generic vocabulary; only the swap-site occurrence must have been replaced.
- **Semantic-preservation check.** A second GPT-5.5 call presents the original and rewritten texts side by side and answers whether the rewrite is content-preserving up to the term swap. Rejections are discarded.
- **Round-trip check.** A third GPT-5.5 call translates the swapped term in its rewrite context back into the source language. The back-translation should match the original surface form up to inflection. Mismatches set `round_trip_flag = true` on the output record but do not cause rejection.

Records failing any non-flag check are not written. The number of rejections per check is recorded in the run summary.

### 4.7 Output schema

`data/idea2/instances.jsonl`, one line per (source row × variant × position) combination:

| field | meaning |
|---|---|
| `instance_id` | unique identifier of the form `{source_row_id}__{variant_tag}__{position}` |
| `source_row_id` | original CSV `id` of the source document |
| `publication_number` | parallel-group identifier |
| `available_languages` | `L_avail(publication_number)` |
| `source_language` | the source row's language; unchanged across variants of the same row |
| `variant_tag` | one of `A_clean`, `B_in_set`, `C_out_of_set`, `D_noisy`, `E_control_nonchem` |
| `position` | one of `title`, `first_sentence`, `body`, or `na` |
| `text` | the (possibly substituted) full document text after concatenation of the original CSV fields |
| `swap_lang` | the language of the inserted term, or null where it does not apply |
| `original_term` | the surface form removed, or null for variant A |
| `swap_term` | the surface form inserted, or null for variant A |
| `swap_char_offset` | character offset of the inserted swap term in the output `text`, or null where it does not apply |
| `chebi_id` | for chemistry substitutions (B, C, D), the ChEBI concept the substituted term refers to; null for clean and control |
| `control_style` | for variant E only, either `in_set` or `out_of_set` |
| `round_trip_flag` | true if the back-translation check produced a mismatch; false otherwise; null where the check does not apply |
| `fallback_flag` | true if a soft preference had to be dropped during selection |

---

## 5. Pipeline stages

| stage | input | output | responsibility |
|---|---|---|---|
| 1 | network access to ChEBI FTP and to the Wikidata SPARQL endpoint | `data/kg/chebi_concepts.parquet` | build the ChEBI chemistry concept table with multilingual aliases (ChEBI native plus Wikipedia titles via Wikidata crosswalk) and neighbor links, populated for concepts relevant to stages 3 and 4 |
| 2 | `data/google_patents/multilingual_corpus.csv` and the output of stage 1 | `data/corpus/documents_linked.parquet` | for every corpus row, concatenate the text fields into the full document text and resolve chemistry mentions in that text to ChEBI IDs |
| 3 | outputs of stages 1 and 2 | `data/idea1/instances.jsonl` | select 50–100 qualifying concepts and compile their gold documents, multilingual aliases, and hard-negative documents |
| 4 | outputs of stages 1 and 2, and OpenAI GPT-5.5 | `data/idea2/instances.jsonl` and `data/idea2/llm_cache.jsonl` | select 50–100 source corpus rows and produce the substituted document variants |

The LLM is invoked only in stage 4. All other stages are deterministic given pinned ChEBI release and Wikipedia dump dates.
