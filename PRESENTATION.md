# Multilingual Chemistry Benchmark Data

> Source for Claude-on-PowerPoint. One slide per `##` heading. Speaker notes are in `>` blockquotes (PowerPoint notes pane, not slide body).

---

## Slide 1 — Title

**Two multilingual chemistry benchmarks, built from patents.**

Inputs:

- **Google Patents — multilingual subset** (1,110 patent documents across EN, DE, ES, FR, ZH; many published as parallel translations of the same patent)
- **ChEBI** — EMBL-EBI's Chemical Entities of Biological Interest ontology, used as the source of chemistry concepts, their multilingual names, and their confusability relations

Two benchmark ideas, each producing ~50–100 instances.

> Speaker notes: we don't build retrievers or measure metrics — those come downstream. We produce the data.

---

## Slide 2 — Idea 1: Alias-Graph Data

**Question:** given a chemistry concept described by its names in several languages, can a retriever find the right document — across languages — while ignoring documents about *chemically similar* concepts?

Each instance contains:

- A multilingual **alias set** for one ChEBI concept (the query side)
- **One gold document** from Google Patents in a chosen language
- Up to **10 hard-negative documents** in **other languages**, each mentioning a *confusable neighbor* of the gold concept (sibling element, conjugate acid/base, tautomer, etc., taken straight from ChEBI's ontology)

> Speaker notes: the cross-lingual gold/negative split is by construction — gold is one language, negatives are different languages. The benchmark explicitly stresses cross-lingual retrieval under chemistry-class confusion.

---

## Slide 3 — Idea 1 example: tin (CHEBI:27007)

**Query (multilingual aliases from ChEBI):**

| EN | DE | ES | FR |
|---|---|---|---|
| tin, Sn | Zinn | estaño | étain |

**Gold document** (`EP-4634425-A1_en`, English): *"...0 % ≤ Lead ≤ 0.2 %, 0 % ≤ Tin ≤ 0.2 %, 0 % ≤ Antimony ≤ 0.2 %..."*

**Hard negatives — different language, confusable neighbor:**

| neg doc | lang | neighbor concept | relation |
|---|---|---|---|
| `EP-4634414-A1_fr` | fr | iron | siblings_role |
| `EP-4630589-A1_de` | de | iron | siblings_role |
| `WO-2025209612-A1_es` | es | zinc | siblings_isa |
| `WO-2025210044-A1_de` | de | carbon | siblings_isa |

> Speaker notes: a naive retriever sees "tin/Zinn/étain/estaño" and might surface the iron/zinc/copper documents instead — that's the failure mode this benchmark exposes.

---

## Slide 4 — Idea 2: Code-Switched Document Data

**Question:** take a real patent and swap **exactly one chemistry term** into another language. Can a retriever still match the document?

Each source patent yields up to **five variant types**:

| variant | what changes |
|---|---|
| **A — clean** | the original document, unchanged (baseline) |
| **B — in-set swap** | one chemistry term swapped into a language the patent IS translated into |
| **C — out-of-set swap** | one chemistry term swapped into a language the patent is NOT translated into |
| **D — noisy** | one chemistry term garbled (typo, hyphen, case noise) in the source language |
| **E — control** | a **non-chemistry** noun swapped to another language — isolates whether the model is sensitive to chemistry specifically or to code-switching in general |

> Speaker notes: B and C also fire at three positions — title, first sentence, body — so up to 9 records per source patent.

---

## Slide 5 — Idea 2 example: patent EP-4634109-A1 (French)

Same source patent, same chemistry concept (`hydrogène` → CHEBI:18276), five variant types in action:

| variant | position | original → swap | target lang |
|---|---|---|---|
| **A** clean | — | (unchanged) | — |
| **B** in-set | title | hydrogène → hydrogen | EN |
| **B** in-set | first_sentence | hydrogène → hydrogen | EN |
| **B** in-set | body | hydrogène → hydrogen | EN |
| **C** out-of-set | title | hydrogène → hidrógeno | ES |
| **C** out-of-set | first_sentence | hydrogène → hidrógeno | ES |
| **D** noisy | body | hydrogène → h-y-drogène | FR (same) |
| **E** control | body | ligne → line | EN (non-chemistry noun) |

Across the full run: 50 source patents → **201 variant records**, covering Chinese, German, Spanish, French and English chemistry terms.

> Speaker notes: same term ("hydrogène") translated to English (in-set, FR has a parallel EN version), translated to Spanish (out-of-set, no ES parallel exists), perturbed in French, and a non-chemistry noun ("ligne" → "line") swapped as a control. The retrieval task: can the model still recognise this as the same patent across all these variants?

---

## Slide 6 — Future work

**1. Refine the prompts and re-examine results.**
Inspect the generated variants and the rejection reasons, then tighten the LLM prompts (and verifier prompts) to lift acceptance rates and improve term quality — especially for low-resource languages and edge-case perturbations.

**2. Add a Q&A layer on top of both benchmarks.**

- **For Idea 1**: generate questions *about the targeted concept* (e.g. "Which document discusses tin alloying in non-oriented electrical steel?"). The gold document is the positive answer; the hard-negative documents (about iron / zinc / carbon) stay negative.
- **For Idea 2**: generate questions *about the original chemistry term, before substitution* (e.g. "Find documents that discuss hydrogen production"). All variants of the source patent — A clean, B in-set, C out-of-set, D noisy, E control — should be classified as **positive**, because they are the same underlying document with only one term changed.

This turns the data from pure retrieval pairs into a question-answering benchmark, and lets us measure how robust a system is to cross-lingual code-switching at the question-document level.

> Speaker notes: the Q&A generation step is itself an LLM task — we'd generate one (or a few) natural-language questions per gold concept and per source patent, then keep the positive/negative labelling we already have.
