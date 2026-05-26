# How the Pipeline Works

A walkthrough of the two benchmarks and how each artifact is built, using **actual rows from the data we just produced**. Every example below comes from `data/idea1/instances.jsonl` or `data/idea2/instances.jsonl`.

---

## TL;DR

| Artifact | Purpose | Rows produced |
|---|---|---|
| [data/kg/chebi_concepts.parquet](data/kg/chebi_concepts.parquet) | ChEBI concept slice (multilingual aliases + neighbor closures) | 1,003 concepts |
| [data/corpus/documents_linked.parquet](data/corpus/documents_linked.parquet) | Every patent document with chemistry mentions resolved to ChEBI IDs | 1,110 docs, 9,634 mentions |
| [data/idea1/instances.jsonl](data/idea1/instances.jsonl) | Benchmark 1 — multilingual alias-graph retrieval data | 80 concepts |
| [data/idea2/instances.jsonl](data/idea2/instances.jsonl) | Benchmark 2 — code-switched document variants | 181 records from 50 source rows |

---

## Stage 1 — The ChEBI knowledge slice

We download the ChEBI flat-file dump from EBI (~360 MB across `chebi.obo`, `compounds.tsv.gz`, `names.tsv.gz`, `structures.tsv.gz`, `chemical_data.tsv.gz`, `relation.tsv.gz`). From those files we extract, for every ChEBI concept that **actually appears in the corpus**:

- **Identity**: `chebi_id`, `chebi_name_en`, `inchikey`, `inchi`, `smiles`, `formula`, `chebi_star`
- **Multilingual aliases**: `aliases_chebi[lang]` — names from `names.tsv.gz` filtered by language column
- **Neighbor closures** (the chemistry "confusability graph"): `siblings_isa`, `siblings_role`, `conjugate_pair`, `tautomers`, `stereo_or_tautomer_inchikey_block`, `parent_hydride_family`

Two-pass approach to keep the slice small:

1. **Bootstrap pass** — build an Aho-Corasick automaton from every ChEBI synonym (per language), scan all 1,110 corpus docs to find which ChEBI concepts are mentioned. Result: 1,003 concepts mentioned in the corpus.
2. **Crawl their neighbors** — for each of those 1,003 concepts, include the union of their neighbor-relation targets in the row.

> The Wikidata→Wikipedia crosswalk (`--with-wikidata`) is **off by default**. The WDQS endpoint kept soft-banning us during testing. ChEBI native synonyms cover EN/DE/ES/FR; only ZH coverage suffers without Wikidata. Pass `--with-wikidata` later when the endpoint cools off.

---

## Stage 2 — Entity linking against the corpus

We concatenate each patent's `title + abstract + description + first_claim + context` into one full document text (offsets in stage 3/4 are relative to this string). Then for each language, we apply the per-language Aho-Corasick dictionary built from `aliases_combined[lang]` with these refinements:

- **Word-boundary matching** for Latin-script languages: prevents "alkohol" from matching inside "alkoholhaltig", which used to inflate mention counts from ~9 k to ~52 k.
- **Case folding** on both text and dictionary (skipped for ZH).
- **Longest-match dedup** with **ambiguity resolution**: when one surface form maps to multiple ChEBI IDs, pick the one whose *other* aliases also appear in the same document.

The result is `documents_linked.parquet` with one row per CSV row. Each row carries `text`, `mentions[]` (each with `surface`, `start`, `end`, `chebi_id`), and the `primary_chebi_id` (most-frequently mentioned in the document).

### How the same concept gets linked in different languages

**There is no parallel-text alignment, no machine translation, and no cross-language co-occurrence model in stage 2.** Each language is linked completely independently of the others. They end up agreeing on the same `chebi_id` because **ChEBI itself is the shared identity layer**: a single ChEBI accession number carries names in many languages, populated by EMBL-EBI curators.

The whole mechanism is one file — `names.tsv.gz` in the ChEBI flat-file dump. Here are the actual rows for `CHEBI:35223` (the "catalyst" concept) in our downloaded dump:

```
ID      COMPOUND_ID  NAME         TYPE        LANGUAGE
108126  35223        catalyseur   SYNONYM     fr
108127  35223        Katalysator  SYNONYM     de
108128  35223        catalizador  SYNONYM     es
59840   35223        catalyst     IUPAC NAME  en
```

Plus the primary English name from `compounds.tsv.gz`:

```
id      name      stars  release_date
35223   catalyst  3      2025-02-21
```

That single shared `COMPOUND_ID = 35223` is the only thing that ties `catalyst`, `catalyseur`, `Katalysator`, and `catalizador` together. ChEBI's chemistry curators have already done the cross-language work.

Stage 1 reads `names.tsv.gz`, groups by `(COMPOUND_ID, LANGUAGE)`, and produces this row in `chebi_concepts.parquet`:

```python
{
  "chebi_id": "CHEBI:35223",
  "chebi_name_en": "catalyst",
  "aliases_chebi": {
    "en": ["catalyst"],
    "de": ["Katalysator"],
    "es": ["catalizador"],
    "fr": ["catalyseur"],
    "zh": [],          # ChEBI has no Chinese synonym for catalyst
  },
  ...
}
```

Stage 2 then inverts each language column into a separate Aho-Corasick automaton (per [dictionary.py](src/multilingual_doc_variants/stage2_link/dictionary.py)):

```
en_dict:  "catalyst"    -> [("CHEBI:35223", "catalyst")]
fr_dict:  "catalyseur"  -> [("CHEBI:35223", "catalyseur")]
de_dict:  "katalysator" -> [("CHEBI:35223", "Katalysator")]
es_dict:  "catalizador" -> [("CHEBI:35223", "catalizador")]
zh_dict:  (no entry for this concept)
```

Now the linking step is **completely monolingual per row.** When stage 2 reads a CSV row, it looks at the row's `language` column and picks the corresponding automaton:

- For `EP-4635013-A1_en` (English), it uses `en_dict`. Aho-Corasick scans the English text, finds the substring `catalyst`, looks it up → returns `("CHEBI:35223", "catalyst")`. A mention is recorded: `{surface: "catalyst", start: 249, end: 257, chebi_id: "CHEBI:35223"}`.
- For `EP-4635013-A1_fr` (French), it uses `fr_dict`. Independently of the English pass, it finds the substring `catalyseur` in the French text, looks it up → returns `("CHEBI:35223", "catalyseur")`. A mention is recorded: `{surface: "catalyseur", start: 278, end: 288, chebi_id: "CHEBI:35223"}`.

These two mention records were produced by two unrelated string scans over two unrelated documents. **They share `CHEBI:35223` purely because ChEBI's `names.tsv.gz` mapped both `catalyst` and `catalyseur` to compound ID 35223.** That's the entire mechanism — there is nothing else.

### What happens when ChEBI has no synonym for a language

ChEBI has zero Chinese synonyms for `CHEBI:35223` (and ZH coverage is near-zero overall — see "ChEBI native synonym coverage" in the status snapshot). Two consequences:

1. **Stage 2's `zh_dict` won't contain `catalyst`** as an entry, so even if a Chinese patent text uses the Chinese word for catalyst (催化剂), stage 2 will *not* link it to `CHEBI:35223`. The mention simply won't appear in our output. That's a recall hole.
2. **Stage 4 variant B targeting ZH** will fail to find a parallel-row mention. It then falls back to `aliases_chebi["zh"]`, finds an empty list, falls back to `wikipedia_titles["zh"]` (also empty since we ran without Wikidata), and **skips the variant**. Running with `--with-wikidata` would fill some of these gaps from Chinese Wikipedia titles.

### Why this design is sufficient for the task

For chemistry, this monolingual-link-via-shared-curated-IDs approach is unusually clean because:

- Chemistry concepts have **canonical identity** (same molecule = same ChEBI ID).
- ChEBI curators **maintain multilingual labels** as part of the database itself.
- Patent texts use **highly specific terminology** — "catalyseur" in chemistry/patent French nearly always refers to the catalyst concept, not a metaphorical usage.

For a domain without a multilingual reference graph (say, general news entity linking), this approach would not work — you'd need either MT-based parallel-text alignment or a multilingual entity model. Here, we get away with five small per-language string-matching dictionaries.

---

## Idea 1 — Alias-Graph Data (Benchmark 1)

### What problem does it pose?

Given a **multilingual alias set** describing a chemistry concept, can a retriever find documents that mention this concept — across languages — *while ignoring confusable-but-different concepts*?

### How an instance is built

For a candidate ChEBI concept to qualify, all of these must hold:

1. Mentioned in the corpus in **≥ 2 distinct languages**
2. Has aliases in **≥ 3 of the 5 target languages** (EN/DE/ES/FR/ZH)
3. Has **≥ 1 confusable neighbor** that is also present in the corpus
4. Prefer `chebi_star == 3` (manually curated), fall back to 2 if needed

Of 1,003 corpus-mentioned concepts, **90 qualified**; we sampled **80** with stratification across coarse type (small_molecule / drug / polymer / salt / biochemical / other).

For each selected concept we then assemble:
- **`gold_documents`** — every corpus doc that mentions this concept, with character offsets of every mention.
- **`hard_negative_documents`** — up to 10 docs that mention a **confusable neighbor** of this concept but NOT the concept itself, tagged with the relation that made them confusable (`siblings_isa`, `siblings_role`, `conjugate_pair`, `tautomers`, `stereo_or_tautomer_inchikey_block`, `parent_hydride_family`).

### Concrete example: `CHEBI:27007` (tin atom)

This is row 1 in [data/idea1/instances.jsonl](data/idea1/instances.jsonl). Picked because it has good 4-language alias coverage, 4 gold docs, 10 hard negatives, 10 neighbor concepts.

**Identity:**
```
chebi_id: CHEBI:27007
inchikey: ATJFFYVFTNAWJD-UHFFFAOYSA-N
```

**Multilingual aliases** (the query side of the benchmark):
```
en: ['50Sn', 'tin', 'Sn']
de: ['Zinn']
es: ['estaño']
fr: ['étain']
```

**Gold documents** — the *correct* retrievals for this concept:

| `id` | lang | mention context |
|---|---|---|
| `EP-4634425-A1_en` | en | `...Lead ≤ 0.2%, 0% ≤ Tin≤ 0.2%, 0% ≤Antimony ≤ 0.2%...` |
| `EP-4634425-A1_fr` | fr | `...0 % ≤ plomb ≤ 0,2 %, 0 % ≤ étain ≤ 0,2 %, 0 % ≤ antimoine...` |
| `WO-2025209952-A2_de` | de | `...(Legierungsmetalle), Rest Zinn und Verunreinigungen...` |

Notice these are not translations of *each other* (the `_en` and `_fr` rows share `publication_number EP-4634425-A1` so they ARE parallel; the `_de` is a different patent). A retriever must find all three despite the surface forms being entirely different (`Tin` / `étain` / `Zinn`).

**Neighbor concepts** — the source of hard negatives:

| neighbor `chebi_id` | name | relation |
|---|---|---|
| `CHEBI:18248` | iron atom | siblings_role |
| `CHEBI:27363` | zinc atom | siblings_role |
| `CHEBI:27594` | carbon atom | siblings_isa |
| `CHEBI:27638` | cobalt atom | siblings_role |
| `CHEBI:15414` | S-adenosyl-L-methionine | siblings_role |

Three of these (iron / zinc / cobalt) are **other metallic elements** — exactly the documents a naïve lexical retriever might confuse with `tin`. The hard-negative pool is built from these:

| neg doc `id` | lang | neighbor that triggered selection | snippet |
|---|---|---|---|
| `EP-4630589-A1_de` | de | CHEBI:18248 (iron) | `...V: ≥ 0,01 bis ≤ 0,20, Rest Eisen, einschließlich üblicher stah...` |
| `EP-4634302-A1_fr` | fr | CHEBI:28694 (copper) | `...polyamide à deux stabilisants de cuivre...` |
| `EP-4634127-A1_en` | en | CHEBI:15414 | `...alloys while converting at the same time aluminium and silicon...` |

A model that retrieves these documents when queried with the "tin" alias set has been **fooled by chemical-class similarity** — which is what the benchmark is meant to expose.

### Stage 3 sampling summary (actual run)

```json
{
  "instances_written": 80,
  "qualifying_concepts": 90,
  "relation_counts": {
    "siblings_isa": 348,
    "siblings_role": 321,
    "conjugate_pair": 1,
    "stereo_or_tautomer_inchikey_block": 4
  },
  "negative_doc_languages": {
    "en": 323, "fr": 169, "de": 142, "es": 40
  }
}
```

`siblings_isa` and `siblings_role` dominate the hard negatives — i.e. confusion within the same chemistry class or with the same functional role. `stereo_or_tautomer_inchikey_block` and `conjugate_pair` are rare in this corpus simply because patent text doesn't often mention isomer pairs.

---

## Idea 2 — Code-Switched Document Data (Benchmark 2)

### What problem does it pose?

Take a real patent document, swap **exactly one chemistry term** into a different language inside the otherwise-untouched source-language text. Can a retriever still match the document to a query in the original language? What about variants where the term is from a language the publication *isn't* even translated into? What about noisy / typo'd terms? And what does *swapping a non-chemistry word* do as a control?

### Source row selection

A corpus row qualifies as a source if it has ≥ 1 chemistry mention and ≥ 200 tokens of text. From 498 qualifying rows we sampled **50** (one per `publication_number`, soft preference for `|L_avail| ∈ {2, 3}`).

### The five variant tags

For each source row, the pipeline tries to emit one record per (variant, position) cell:

| Variant | What changes | Positions |
|---|---|---|
| `A_clean` | Nothing — original text as-is | `na` |
| `B_in_set` | A chemistry term swapped to a language **inside** `L_avail` for this publication number | `title`, `first_sentence`, `body` |
| `C_out_of_set` | A chemistry term swapped to a language **outside** `L_avail` (no parallel translation exists) | `title`, `first_sentence`, `body` |
| `D_noisy` | A chemistry term *perturbed* (typos, hyphen insertion, case noise, Greek-letter swaps, etc.) — no LLM | `body` |
| `E_control_nonchem` | A **non-chemistry** noun swapped to another language (control: not about chemistry at all) | `body` |

So up to 9 records per source row. In practice we got **181 records from 50 source rows = 3.6 avg/row** — many skips happen because the chemistry mention isn't in the requested position (e.g., title rarely has a chemistry term).

### Substitution mechanics

This is the key architectural decision: **the pipeline does the swap, not the LLM.** Below is exactly what happens, traced through real data.

#### Step 1 — Find the term to remove (no LLM)

Stage 2 has already linked every chemistry mention in every document. So when we open the source row `EP-4635013-A1_en` from [data/corpus/documents_linked.parquet](data/corpus/documents_linked.parquet), we already have a list of `mentions[]` like:

```python
[
  {"surface": "catalyst", "start": 193, "end": 201, "chebi_id": "CHEBI:35223"},
  {"surface": "catalyst", "start": 249, "end": 257, "chebi_id": "CHEBI:35223"},
  {"surface": "ion",      "start": 326, "end": 329, "chebi_id": "CHEBI:24870"},
  ...  # 26 total in this row, 10 of them for CHEBI:35223
]
```

The mention picker in [mention_picker.py](src/multilingual_doc_variants/stage4_idea2/mention_picker.py) then:

1. **Filters by position.** Each position (`title` / `first_sentence` / `body`) corresponds to a character-offset range computed by [positions.py](src/multilingual_doc_variants/stage4_idea2/positions.py) on the same concatenated text. We keep only mentions whose `start` falls in the requested range. For variant `B_in_set` at position `body`, we discard mentions in the title and first sentence.
2. **Applies soft preferences** (per spec §4.4):
   - Corpus-wide mention frequency for that `chebi_id` must be ≥ 3 (otherwise the concept is too rare to make an interesting query).
   - For variant B specifically: the same `chebi_id` must also be mentioned in the parallel-language row (the "term is widely used in that translation" requirement).
3. **Drops preferences one at a time** if no candidate remains, marking `fallback_flag = true` when it does.
4. **Random pick** (seeded by `row_id × variant × position`) from the surviving pool.

For the `EP-4635013-A1_en` body, that picked the mention at offset `[249:257]` → `surface = "catalyst"`, `chebi_id = CHEBI:35223`. We now know *what* to remove and *exactly where*.

#### Step 2 — Compute the substitute term

This step branches on the variant tag. Crucially, **none of these branches needs the LLM to perform the swap itself**; the data needed lives either in our stage-2 output or our stage-1 KG slice.

**Variant B (in-set):** the substitute is **read directly from the parallel-language patent row.**

For our example, `EP-4635013-A1` has `L_avail = {en, fr}`. We pick `swap_lang = fr` (uniform random from `L_avail − {source_lang}`). The pipeline then opens the parallel row `EP-4635013-A1_fr` and looks at *its* mentions:

```python
[
  {"surface": "catalyseur", "start": 212, "end": 222, "chebi_id": "CHEBI:35223"},
  {"surface": "catalyseur", "start": 278, "end": 288, "chebi_id": "CHEBI:35223"},
  ...  # 10 mentions of CHEBI:35223 in the French parallel row
]
```

These mentions exist because stage 2's Aho-Corasick automaton, built from the French aliases for CHEBI:35223 (`["catalyseur"]`), matched the French patent text. So when we ask "what surface form does this same concept take in the French translation?", we read it off directly — no translation call needed.

The preference order ([b_in_set.py](src/multilingual_doc_variants/stage4_idea2/variants/b_in_set.py)):
1. A parallel-row surface whose casefold equals the ChEBI primary name in `swap_lang` (`aliases_chebi[fr][0]` = `"catalyseur"`) — picked first.
2. Any other parallel-row surface for the same `chebi_id`.
3. **Fallback:** `aliases_chebi[swap_lang][0]` from the KG slice; mark `fallback_flag = true`.

In our trace, step 1 hits — `swap_term = "catalyseur"`.

**Variant C (out-of-set):** there is no parallel row, so we go straight to the KG. From [c_out_of_set.py](src/multilingual_doc_variants/stage4_idea2/variants/c_out_of_set.py):

For the same example, `swap_lang` is sampled from `{en,de,es,fr,zh} − L_avail = {de, es, zh}` — say we get `es`. We look up the same `chebi_id` (`CHEBI:35223`) in the KG slice and read:

```python
aliases_chebi = {
  "en": ["catalyst"],
  "de": ["Katalysator"],
  "es": ["catalizador"],
  "fr": ["catalyseur"],
  "zh": [],
}
```

`swap_term = aliases_chebi["es"][0] = "catalizador"`. Done. (If `aliases_chebi[swap_lang]` is empty — common for ZH because ChEBI has no Chinese synonyms — we fall back to `wikipedia_titles[swap_lang]`, which is empty in this run since we ran without Wikidata, so the variant gets skipped.)

**Variant D (noisy):** stays in the source language. The substitute is just the original surface form passed through `perturb()` in [d_noisy.py](src/multilingual_doc_variants/stage4_idea2/variants/d_noisy.py). It samples 1–2 perturbations from a fixed catalog:

| Perturbation | Example |
|---|---|
| Hyphenation swap | `catalyseur` → `catalys-eur` |
| Typo (delete/swap) | `solvant` → `solvnt`, `ion` → `oin` |
| Case noise (~30% per char) | `cobalt` → `coBalt` |
| Greek-letter swap | `α-tocopherol` → `alpha-tocopherol` or `a-tocopherol` |
| Oxidation-state swap | `Fe(III)` ↔ `Fe3+` |
| Locant whitespace | `1,2-dichloroethane` → `1 2-dichloroethane` |
| Sub/superscript loss | `H₂O` → `H2O` |

No translation, no LLM, no network — just deterministic string manipulation with a seeded RNG.

**Variant E (control non-chem):** the *only* variant that may actually call the LLM during substitution, and only for the `out_of_set` translation. The non-chemistry noun itself is found *without* LLM:

1. Run a spaCy multilingual POS tagger over the source text.
2. Pick a noun whose span does **not** overlap with any chemistry mention (so it's guaranteed to be non-chemistry).
3. If `control_style = in_set` (target language is in `L_avail`), look up the equivalent noun in the parallel row by aligning sentence context — pure string matching, no LLM.
4. If `control_style = out_of_set` (target language not in `L_avail`), fall back to a small cached gpt-4o-mini translation. This is the one LLM call in E's substitution path.

#### Step 3 — Splice (no LLM)

Once we have `(original_start, original_end, swap_term)`, the substitution is one line:

```python
rewrite = source_text[:original_start] + swap_term + source_text[original_end:]
```

For our running example:

```
source_text[:249]  +  "catalyseur"  +  source_text[257:]
                                ↑                  ↑
                          replaces "catalyst"     resumes immediately after
```

Result, around the swap site:

```
...at least two recombination catalyseur layers, each of the at least
two recombination catalyst layers comprising a recombination catalyst
and a first ion exchange material...
```

Note: **only the picked occurrence at offset 249 was replaced.** The other 9 occurrences of `catalyst` in the same document are deliberately left intact — the spec explicitly allows this ("The form may legitimately appear elsewhere if it is also generic vocabulary; only the swap-site occurrence must have been replaced"). This is exactly the behavior we couldn't get reliably out of an LLM-driven rewrite, which is why we switched to deterministic splicing.

#### Why we skipped LLM-driven rewriting (for B/C/E)

The spec originally calls for the LLM to perform a "fluent edit" so grammar around the inserted term agrees (German declension, French gender, etc.). We tried two iterations of this:

1. **gpt-5 (reasoning model).** Eight out of nine rewrites came back empty — the `max_completion_tokens=2500` budget was consumed by hidden reasoning tokens, leaving no visible output.
2. **gpt-4o-mini with explicit "modify only around the inserted term" instructions.** The model would either replace every occurrence of the source term in the whole document (e.g., all 10 `starch` → `amidon`), or, on long passages, fail the term-presence check by silently re-casing the swap term.

Deterministic splicing avoids both classes of failure entirely and is far cheaper. The cost is that the inserted term may not have perfect grammatical agreement with its surroundings — acceptable for a code-switched POC where the **information value** is in the term substitution, not in the fluency of the article preceding it.

#### What the LLM still does

After the deterministic splice, gpt-4o-mini runs two verifier calls per B/C/E record:

1. **Semantic-preservation check** — both texts side-by-side, yes/no on "is this content-preserving up to the term swap?" Rejected records are dropped (76 rejections in the 50-row run).
2. **Round-trip translation** — translate the swap term back to the source language *in its rewrite context* and compare against the original surface form. Mismatch sets `round_trip_flag = true` but does not reject.

The term-presence check (swap term must appear exactly once, case-insensitive) runs in pure Python.

### Concrete example: source row `EP-4635013-A1_en`

This row is about a **proton-exchange membrane for water electrolysis with recombination catalysts and ion-exchange materials.** It's English; its parallel set is `L_avail = {en, fr}` (French translation exists, the rest do not). The pipeline emitted 6 variants from it:

| variant | position | original → swap | swap_lang | snippet of swap site |
|---|---|---|---|---|
| **A_clean** | na | — | — | (text unchanged) |
| **C_out_of_set** | title | `proton` → `protón` | es | `...Improved multi-layered protón exchange membrane for water electrolysis...` |
| **B_in_set** | first_sentence | `catalyst` → `catalyseur` | fr | `...at least two recombination catalyseur layers are separated by...` |
| **B_in_set** | body | `catalyst` → `catalyseur` | fr | `...at least two recombination catalyseur layers are separated by...` |
| **C_out_of_set** | body | `catalyst` → `catalizador` | es | `...at least two recombination catalizador layers are separated by...` |
| **D_noisy** | body | `ion` → `oin` | en | `...comprising a first oin exchange material...` |

Notice:
- **B_in_set** picks French (`fr`) because French is in `L_avail`. The swap term `catalyseur` was pulled from the parallel French row's actual usage.
- **C_out_of_set** picks Spanish (`es`) because Spanish is *not* in `L_avail` (no Spanish translation of this patent exists). The swap term `protón`/`catalizador` came from the ChEBI Spanish alias list for the same concept.
- The **title** position only fires for C here (the title mentions `proton`, picked up by a non-EN swap); the **first_sentence** position only fires for B (first sentence mentions `catalyst`).
- **D_noisy** picked `ion` and applied a typo perturbation → `oin` (anagrammed). Other examples from the same run:
  ```
  catalyseur  →  catalys-eur   (hyphenation)
  oxygen      →  oxyg-en
  catalyst    →  catalyts      (typo: missing letter)
  aluminum    →  alumin-um
  solvant     →  solvnt        (vowel drop)
  cuivre      →  cui-vre
  ```

### Concrete example: variant E (non-chemistry control)

E swaps a noun that is **deliberately not a chemistry mention**. We use a spaCy multilingual POS tagger to find nouns, exclude any that overlap with a ChEBI mention, then translate. Both *in-set* (target language is in `L_avail`) and *out-of-set* styles appear; the choice is recorded in `control_style`.

Real examples from the run:

| original → swap | langs | style |
|---|---|---|
| `active` → `actif` | en→fr | in_set |
| `components` → `composants` | en→fr | in_set |
| `étant` → `being` | fr→en | in_set |
| `conversion` → `Umwandlung` | en→de | out_of_set |
| `Abstract` → `Zusammenfassung` | en→de | out_of_set |
| `concerne` → `涉及` | fr→zh | out_of_set |
| `disclosure` → `披露` | en→zh | out_of_set |
| `exemple` → `Beispiel` | fr→de | out_of_set |

A retriever that handles these E variants as well as it handles A_clean is showing it's *generally* code-switch-tolerant, not specifically *chemistry-term-tolerant* — which is what the benchmark wants to isolate.

### Verification — what the LLM still does

For each B/C/E record, two LLM calls run after the deterministic swap:

1. **Semantic-preservation check** — paste both texts side by side, ask "is the rewrite content-preserving up to the term swap?" yes/no. A `no` causes rejection (76 rejections in the 50-row run — the verifier model is conservatively strict).
2. **Round-trip translation** — translate the swap term, in its rewrite context, back into the source language and check that it matches the original. Mismatches set `round_trip_flag = true` but do **not** reject the record.

A third check, **term-presence** (must appear exactly once, case-insensitive), runs without LLM. 18 records were rejected for this reason — usually when the swap term coincidentally appears elsewhere in the document.

### Stage 4 summary (actual run)

```json
{
  "source_rows": 50,
  "records_written": 181,
  "skips": {
    "B/title": 45, "C/title": 48,
    "B/first_sentence": 32, "C/first_sentence": 41,
    "B/body": 31, "C/body": 42,
    "E/body": 30
  },
  "rejections": {
    "semantic_preservation": 76,
    "term_presence": 18,
    "e_no_translation": 5
  },
  "model": "gpt-4o-mini"
}
```

Title and first-sentence positions skip very often because chemistry terms cluster in the body / first-claim / context sections of patents, not in titles. The 76 `semantic_preservation` rejections are the largest quality lever — they happen when the verifier model decides the swap meaningfully changed the meaning (often a false alarm, conservative-bias of gpt-4o-mini).

---

## Reproducibility

- Fixed RNG seed (`42`) for every sampling step.
- ChEBI release pinned via `data/kg/chebi_raw/release_metadata.json` (each file's HTTP Last-Modified header).
- LLM calls content-hashed and cached to [data/idea2/llm_cache.jsonl](data/idea2/llm_cache.jsonl) — rerunning stage 4 with the same source rows costs $0.
- All deterministic stages (1, 2, 3) complete in under 15 seconds end-to-end on the cached ChEBI dump.

## CLI quick reference

```bash
uv run mdv stage1                       # build the KG slice
uv run mdv stage1 --with-wikidata       # add Wikipedia titles via WDQS (slow, throttled)
uv run mdv stage2                       # link mentions
uv run mdv stage3                       # build Benchmark 1
uv run mdv stage4 --limit 50            # build Benchmark 2 (LLM calls cached)
uv run mdv all                          # all four stages in sequence
```
