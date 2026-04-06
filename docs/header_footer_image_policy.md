# Header/Footer And Image Policy

Updated: 2026-04-06

This document translates recent public/internal evaluation findings into a
concrete output policy for ParserX. The goal is to make later code changes
incremental and testable instead of relying on vague notions such as
"preserve more layout".

## Problem Statement

Recent four-tool comparisons showed a split:

- ParserX is often strongest on native text and native table extraction.
- LlamaParse is often preferred by human readers on finance/report PDFs where
  page identity, charts, and mixed layout matter.

The biggest user-facing gaps are no longer "can we extract text at all?" but:

- when should repeated page furniture be kept?
- what counts as document identity metadata vs clutter?
- which images should remain in Markdown?
- when should image-derived text/tables replace the image itself?
- how should charts be represented so the Markdown still feels complete?

## Design Goals

- Preserve document identity without flooding the body with repeated headers.
- Keep charts and meaningful figures visible to users.
- Avoid redundant output when image content is already captured as text/table.
- Use stable linked assets, not base64 blobs.
- Keep chapter detection and image retention from fighting each other.

## Header/Footer Policy

### Two-Class Model

ParserX should no longer treat repeated top/bottom content as one category.
Instead, classify page furniture into:

1. `identity_metadata`
2. `decorative_or_repeated_furniture`

### Identity Metadata

This class includes page-top or page-bottom content that helps a reader answer:

- what document is this?
- who issued it?
- when was it produced?
- what is its recommendation/status?

Typical examples:

- broker / organization name
- report date
- recommendation label
- issue / report type
- analyst block
- title-page data box

### Decorative Or Repeated Furniture

This class includes:

- repeating logo-only banners
- page numbers
- repeated lines or separators
- navigation chrome
- website footer boilerplate

### Retention Rules

Preferred policy:

- keep first-page identity metadata when confidence is high
- remove repeated decorative furniture everywhere
- on later pages:
  - remove repeated identity blocks if they add no new information
  - keep them only when confidence is low and removing them risks losing key
    metadata entirely

Fallback-safe policy:

- if the classifier is unsure whether a block is important metadata, bias
  toward keeping it rather than deleting it

### Interaction With Chapter Detection

Retained identity blocks must not become headings by mistake.

Implementation guidance:

- mark retained header/footer blocks with metadata such as:
  - `retained_page_identity=true`
  - `exclude_from_heading_detection=true`
- chapter detection should ignore these blocks unless a later stage explicitly
  reclassifies them

### Semi-Automatic Checks To Add

- warn when first-page identity metadata appears in OCR/native extraction but
  disappears in final Markdown
- warn when the same retained identity block repeats on too many pages
- warn when retained page identity becomes a heading

## Image Policy

### Image Classes

Every image should be assigned to one of these classes:

1. `decorative`
2. `text_only`
3. `table_only`
4. `chart_or_plot`
5. `mixed_informational`
6. `uncertain`

### Desired Output By Class

`decorative`
- do not keep image in Markdown
- do not describe unless explicitly requested

`text_only`
- extract OCR text into body
- usually do not keep image itself
- avoid duplicate image placeholder if body already contains the text

`table_only`
- extract as Markdown table if reliable
- usually do not keep image itself
- keep the image only when table structure is too uncertain to trust

`chart_or_plot`
- keep image link in Markdown
- keep chart title/caption
- add concise chart description
- add extracted chart summary or approximate table when reliable

`mixed_informational`
- keep image link in Markdown
- add concise description
- integrate OCR/table/text evidence into nearby body blocks when useful

`uncertain`
- bias toward keeping the image link
- attach minimal description
- avoid assertive structured extraction unless confidence is acceptable

## Markdown Contract For Images

ParserX should write image artifacts to a stable subdirectory:

```text
output_dir/
  index.md
  images/
    figure_001.png
    chart_001.png
```

Markdown should reference images via relative links, for example:

```md
![常熟银行与沪深300指数行情走势图](images/chart_001.png)
```

Never:

- inline base64
- point to temp directories
- reference deleted scratch files

## Placeholder Text Policy

Internal processing messages must never leak into final Markdown.

Examples to suppress:

- `Text content preserved in OCR body text.`
- internal routing notes
- internal confidence/debug notes

If a short user-facing explanation is genuinely needed, it should read like
content, not a system trace.

Bad:

```md
> [图片] Text content preserved in OCR body text.
```

Better:

```md
![图表标题](images/chart_001.png)

图表展示了常熟银行与沪深300指数的区间走势。
```

## Chart Policy

### Minimum Acceptable Chart Output

For a meaningful chart, ParserX should preserve at least:

- the chart title or caption
- a linked image asset
- a one- or two-sentence description

### Better Output

When extraction confidence is sufficient, also provide:

- an approximate value table
- a legend summary
- start/end trend description

### Important Safety Rule

Chart-derived values should not be presented as exact unless confidence is high.

Preferred wording for low-confidence extraction:

- "approximate chart values"
- "estimated from chart labels"

Avoid silently presenting guessed values as authoritative facts.

## Reading-Order Integration

Image-derived text/tables should appear near the closest relevant context:

- nearest caption
- nearest section heading
- nearest figure reference

Avoid:

- emitting all figure-derived tables at the top of the document
- placing chart blocks before the title page metadata
- separating image descriptions far away from the image link

## Internal Dataset Lessons

From the current `ground_truth/` internal set:

- `pdf_text01_tables` confirms ParserX should keep investing in native and
  cross-page table quality; this is already a core strength.
- `text_table01` shows ParserX still leaves visible line-wrap artifacts in
  ordinary body text, even when content fidelity is high.
- `deepseek` suggests that webpage-like screenshots need a separate policy:
  preserve useful page identity, but avoid noisy UI chrome.
- `text_table_libreoffice` shows ParserX performs well on clean office-export
  documents, but could still improve formatting smoothness.

## Implementation Sequence

Recommended order:

1. Add metadata classes for retained page identity vs removable furniture.
2. Stop leaking internal placeholder text into final Markdown.
3. Save linked image assets in stable `images/` subdirectories.
4. Add image classes and class-specific retention rules.
5. Add chart-specific retention and chart summary blocks.
6. Add semi-automatic checks for the new behavior.
