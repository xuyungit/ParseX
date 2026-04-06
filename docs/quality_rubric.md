# Output Quality Rubric

Updated: 2026-04-06

This document defines what "high-quality Markdown output" means for ParserX.
It is intentionally broader than text-similarity scoring, because users often
judge quality by readability, document completeness, and preservation of
important page-level signals.

## Why This Exists

Pure automatic metrics are necessary, but not sufficient.

A parser can score well on text overlap while still producing Markdown that is
hard to read or missing important document identity signals such as:

- broker name / report date / rating banner
- figure captions and chart titles
- useful inline emphasis
- image placeholders and linked assets
- chart or image-derived tables that users expect to see in the body

For that reason, ParserX should be evaluated with three layers:

1. Core automatic fidelity metrics
2. Semi-automatic product-quality heuristics
3. Human review on the remaining ambiguous cases

## Quality Dimensions

### 1. Document Identity Retention

The output should preserve the document-level signals that help a reader
identify what the file is and how it should be interpreted.

Examples:

- title page or report banner
- issuer / organization / broker name
- report date
- recommendation / status labels
- analyst / author block

High quality:
- key identity fields are preserved at least once
- repeated page furniture is not allowed to drown the body
- document provenance is still obvious from the Markdown alone

Low quality:
- all headers/footers are removed, including important first-page metadata
- or every page header is repeated so aggressively that readability collapses

### 2. Structural Readability

The output should read like a document, not a dump of text fragments.

High quality:
- major sections are represented as headings
- side panels and data boxes appear in plausible reading order
- chart, table, and appendix blocks are attached to the surrounding context

Low quality:
- headings are flattened into plain text
- sidebars or data panels are emitted before the title with no structure
- chart blocks are detached from their explanatory text

### 3. Text Fidelity

The wording should remain faithful to the source. Reformatting is fine; silent
rewriting is not.

High quality:
- OCR corrections improve readability without changing meaning
- punctuation normalization is acceptable
- formulas and identifiers remain stable

Low quality:
- model-generated paraphrase replaces source text
- numbers, company names, or recommendations drift
- duplicate paragraphs appear because image OCR and body OCR were both emitted

### 4. Table Fidelity

Tables should become readable structured data in Markdown whenever possible.

High quality:
- native tables render as Markdown tables
- image-derived tables are inserted into the body near the relevant text
- multi-row headers and merged cells keep their essential semantics

Acceptable fallback:
- HTML table only if structure would otherwise be lost

Low quality:
- table disappears entirely
- table remains only as prose summary when actual values matter
- table text is emitted as a flat paragraph or blockquote without structure

### 5. Image and Chart Handling

Images are first-class content, but should not be retained blindly.

Desired policy:
- keep meaningful images
- skip decorative images
- if an image is only text or only a table, extract the content into the body
  and usually omit the image itself
- for charts and mixed informational figures, keep both:
  - a linked image asset
  - a concise description
  - structured extraction when reliable

High quality:
- Markdown references linked image files, not base64
- saved image assets live beside the Markdown in a stable subdirectory
- chart title and main message are preserved

Low quality:
- image is dropped even though it carries unique information
- image is kept even though its full content already appears as text/table,
  creating obvious visual redundancy
- output contains vague placeholder text instead of a meaningful explanation

### 6. Formatting Fidelity

ParserX is not required to clone page layout, but it should preserve useful
format signals that help interpretation.

High quality:
- bold or heading-like emphasis is preserved where materially useful
- formulas remain readable and preferably normalized to LaTeX
- list bullets stay lists

Low quality:
- all emphasis is flattened
- formulas degrade into noisy symbol soup
- headings lose level and become body text

### 7. Markdown Usability

The final artifact should be directly usable downstream.

High quality:
- clean Markdown with stable headings, tables, and links
- linked image paths resolve locally
- no unexplained internal placeholder text

Low quality:
- HTML fragments dominate where Markdown was expected
- temp paths or vanished assets are referenced
- diagnostic text leaks into the final document

## What Should Be Automated

The following checks should move out of human review when feasible:

- first-page identity retention:
  - title
  - issuing organization / broker
  - date
  - rating / recommendation labels
- duplicate-content ratio between body text and image-derived OCR
- image placeholder quality:
  - unresolved placeholders
  - leaked internal messages such as "Text content preserved in OCR body text."
- image asset coverage:
  - placeholder exists but linked file is missing
  - linked file exists but is never referenced
- HTML-block leakage:
  - count of `<table>` or other raw HTML blocks in outputs intended to be Markdown-first
- chart retention:
  - figure title preserved
  - chart asset linked
  - optional extracted table or summary present
- reading-order sanity heuristics:
  - title should not appear after large body sections
  - first-page identity block should not be deleted entirely

These checks will never fully replace human review, but they can sharply reduce
what humans need to inspect manually.

## What Still Needs Human Review

The following items remain subjective or document-dependent:

- whether a repeated header is useful metadata or distracting furniture
- whether a chart extraction is "helpful enough" to keep
- whether preserving a figure is worth the visual redundancy
- whether a section ordering feels natural in a mixed layout
- whether formatting loss is acceptable for the target use case

## Preferred Output Contract For Images

Future ParserX output should follow this contract:

- save images/screenshots under a stable subdirectory such as `images/`
- reference them from Markdown using relative links
- never inline image bytes as base64
- attach a human-readable description only when it adds value
- suppress decorative images
- suppress text-only or table-only images when their content has already been
  faithfully extracted into the body
- keep charts and mixed-information figures when they carry unique visual value

## High-Quality Output Bar

We should consider an output "high quality" only when all of the following are
true:

- the document is still identifiable from the Markdown
- the reading order is natural enough for a human to follow
- the main body text is faithful to the source
- tables and chart data are preserved in a useful structured form
- images are linked when they carry value, and omitted when they are pure
  redundancy
- no internal debug or placeholder text leaks into final output
