# The Two Benchmarks — Algorithm

This document describes *what* each benchmark is and *how* its instances are
constructed, at the level of the algorithm only. It says nothing about the code,
files, or libraries that implement it.

Both benchmarks start from the same two ingredients:

1. **A multilingual patent corpus** — the same patents published in several
   languages. Each document is one patent in one language.
2. **A chemistry knowledge graph** — every chemistry concept has a stable
   identity, a set of names in multiple languages, and links to *neighboring*
   concepts (things it is a kind of, things that play the same role, isomers,
   conjugate acid/base partners, and so on).

A prerequisite shared by both: every chemistry term in every document has been
**resolved to a concept identity**. Because the knowledge graph supplies the same
identity to a concept's English, German, Spanish, French, and Chinese names, a
term found in a German document and a term found in a French document can carry
the *same* concept id even though the surface words look nothing alike. This
cross-language identity is what makes both benchmarks possible.

---

## Idea 1 — Alias-Graph Retrieval

### What we wanted

We wanted to pick a **concept**, gather the documents that genuinely talk about
it (the *gold* documents), and surround it with **hard negatives** — documents
that look chemically similar but are actually about a *different* concept. The
benchmark then asks: given only the concept's multilingual name set as a query,
can a retriever find the gold documents across languages **without being fooled**
by the look-alikes?

### The algorithm

**Step 1 — Decide which concepts are worth using.**
A concept qualifies as the centre of an instance only if all of the following
hold:

- It is **mentioned in the corpus in at least two different languages.** (If it
  only ever appears in one language, there is no cross-lingual challenge.)
- It has **names in at least three of the five target languages.** (The query
  side of the benchmark is the multilingual name set, so it must be rich enough.)
- It has **at least one neighboring concept that also appears in the corpus.**
  (Without a confusable neighbor present, we cannot build hard negatives.)
- It is **well-curated.** We prefer manually-curated concepts and fall back to
  lightly-curated ones only if needed; uncurated concepts are excluded.

**Step 2 — Sample a balanced set of these concepts.**
From the qualifying pool we draw the target number of concepts, but not at
random. We **stratify** the draw so the final set is spread across coarse
chemistry types (small molecule, drug, polymer, salt, biochemical, other) and
across how many languages each concept has names in. This prevents the benchmark
from collapsing onto one easy category. Curated concepts are drawn first.

**Step 3 — Build the gold side.**
For the chosen concept, the **gold documents are exactly the corpus documents
that actually mention it.** Each gold document records *where* the concept is
mentioned. These documents are spread across languages — and crucially the
correct answers are not translations of one query word, they are the *same
concept* expressed in entirely different surface forms in different languages.

**Step 4 — Build the hard-negative side.**
This is the heart of the idea. We look at the concept's **neighbors** in the
knowledge graph — concepts it is easily confused with:

- siblings under the same parent (same chemical class),
- concepts that play the same functional role,
- conjugate acid/base partners,
- tautomers and stereo/isomer relatives.

For each such neighbor that is present in the corpus, we collect documents that
**mention the neighbor but do *not* mention the target concept.** These become
the hard negatives, each tagged with the relation that made it confusable. A
retriever that returns these has been tricked by chemical-family similarity
rather than actually identifying the concept — which is precisely the failure the
benchmark is built to expose.

**Step 5 — Assemble the instance.**
Each instance is therefore: the concept's identity, its multilingual name set
(the query), the gold documents, the hard-negative documents (with their
confusability relations), and the list of neighbor concepts used. Concepts for
which no cross-lingual hard negative could be found are dropped.

### What it measures

Whether a retriever can match a concept **across languages** using only its name
set, while **rejecting documents about chemically-adjacent but distinct
concepts.**

---

## Idea 2 — Code-Switched Document Variants

### What we wanted

We wanted to take a **real patent document** and make a family of controlled
variants of it, each differing from the original by **exactly one swapped term.**
The benchmark then asks: if a single chemistry word inside an otherwise-untouched
document is replaced by its form in another language (or perturbed, or replaced
by a non-chemistry word as a control), can a retriever still recognize the
document? The single-term edit isolates *one* effect at a time.

### The algorithm

**Step 1 — Choose source documents.**
A document qualifies as a source if it actually mentions at least one chemistry
concept and is long enough to be a realistic retrieval target. From the
qualifying pool we sample the target number, taking **one document per patent**
(so no patent dominates) and softly preferring patents that exist in a couple of
languages, since those enable the "in-set" swap described below.

**Step 2 — Know each patent's available languages.**
For every patent we record the set of languages it was actually published in.
Call this its *available set.* This split — languages the patent *has* a
translation in versus languages it does *not* — drives two of the variants.

**Step 3 — For each source document, produce one variant per recipe.** Each
variant keeps the whole document identical except for a single chosen term:

- **A — Clean.** The original document, unchanged. The baseline.

- **B — In-set swap.** Pick a chemistry term in the document and replace it with
  the form that **same concept** takes in another language that the patent *does*
  have a translation in. The substitute is the term as it genuinely appears in
  that parallel translation. This simulates code-switching to a language the
  document is "supposed" to exist in.

- **C — Out-of-set swap.** Same idea, but the replacement language is one the
  patent was **not** translated into. There is no parallel document to read the
  term from, so the substitute is the concept's standard name in that language
  taken from the knowledge graph. This simulates code-switching to a "foreign"
  language for this document.

- **D — Noisy.** Keep the term in its original language but **perturb its
  spelling** — typos, an inserted hyphen, case noise, Greek-letter spelled out,
  oxidation-state notation swapped, and the like. This tests robustness to
  surface noise rather than to language.

- **E — Non-chemistry control.** Swap a word that is **deliberately not a
  chemistry term** — an ordinary noun that does not overlap any chemistry
  mention — into another language. If a retriever copes with E as well as it
  copes with the chemistry swaps in B and C, then its tolerance is *general*
  code-switch tolerance, not specifically chemistry-term tolerance. E isolates
  that distinction.

**Step 4 — Choose the term, and its position.**
For the swap variants we also vary *where* in the document the swapped term sits
— title, first sentence, or body — because a term's position changes how much a
retriever relies on it. Among the eligible mentions we prefer concepts that are
not too rare, and (for the in-set variant) concepts that are confirmed to be used
in the parallel translation too. When no candidate satisfies a preference, the
preference is relaxed and the instance is flagged as a fallback.

**Step 5 — Make the edit minimal and exact.**
Only the **one chosen occurrence** of the term is replaced; if the same word
appears elsewhere in the document it is left alone. This is deliberate: the
variant must differ from the original by a single, locatable edit, so that any
change in retrieval behaviour can be attributed to that one swap and nothing
else.

**Step 6 — Verify each variant.**
A variant is only kept if it survives three checks:

- **Term presence** — the swapped term must appear in the new document, exactly
  once, at the intended spot.
- **Meaning preservation** — the edit must not have changed what the document is
  about; it should be the same content up to the single term swap. Variants that
  alter the meaning are discarded.
- **Round-trip** — translating the swapped term back into the original language
  should recover the original word. A mismatch does not discard the variant but
  is flagged, since it signals a looser substitution.

Variants that fail an unrecoverable check are dropped and counted, so the final
set is clean.

### What it measures

Whether a retriever still recognizes a document when **exactly one term** is
code-switched into another language (in-set or out-of-set), perturbed, or — as a
control — when a non-chemistry word is switched instead. Comparing performance
across A/B/C/D/E isolates *which* kind of variation actually hurts retrieval.
