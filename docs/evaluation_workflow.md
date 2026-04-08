# Evaluation Document Workflow

Standard process for adding a new test document to `ground_truth/` and
establishing its evaluation baseline.

## 1. Add the source document

```bash
mkdir -p ground_truth/<doc_name>
cp /path/to/source.pdf ground_truth/<doc_name>/input.pdf
# or for Word documents:
cp /path/to/source.docx ground_truth/<doc_name>/input.docx
```

Supported formats: `.pdf`, `.docx`, `.doc` (`.doc` is auto-converted to
`.docx` via LibreOffice at parse time).

Naming convention: `<type>_<description>`, e.g. `text_code_block`,
`paper01`, `ocr_scan_jtg3362`, `text_report01`.

## 2. Generate expected.md baseline

Use LlamaParse (agentic tier) to generate the initial baseline, then
manually correct obvious errors.

```bash
# Ensure LLAMA_CLOUD_API_KEY is set (in .env)
npx tsx scripts/llamaparse_to_markdown.ts \
  --input ground_truth/<doc_name>/input.pdf \
  --output ground_truth/<doc_name>/expected.md \
  --metadata /tmp/<doc_name>_llamaparse_metadata.json
```

### Manual correction guidelines

- **Short documents (< 5 pages)**: review the entire output and fix all
  structural issues (heading levels, code fences, list formatting, table
  structure, spurious page-break artifacts like `---`).
- **Long documents (5+ pages)**: spot-check the first 3-4 pages and the
  last page. Fix obvious structural issues. Accept minor imperfections —
  the baseline can be refined incrementally as ParserX improves.
- **Common LlamaParse artifacts to fix**:
  - `---` horizontal rules from page breaks → remove
  - `<sup>` / `<sub>` HTML tags → normalize or keep if appropriate
  - Overly long inline code → convert to fenced code blocks
  - Kangxi radical characters (⽤→用, ⾃→自) → normalize to standard CJK
  - Duplicate or out-of-order content → reorder or deduplicate

Save the corrected file as `ground_truth/<doc_name>/expected.md`.

## 3. Run ParserX and compare

```bash
# Run ParserX (works with .pdf, .docx, or .doc)
psx parse ground_truth/<doc_name>/input.pdf --out /tmp/<doc_name>_parserx
# or:
psx parse ground_truth/<doc_name>/input.docx --out /tmp/<doc_name>_parserx

# Run eval (compares output against expected.md)
psx eval ground_truth/<doc_name>

# If needed, inspect the output directly
cat /tmp/<doc_name>_parserx/output.md
```

Note: For DOCX documents, the pipeline automatically skips geometry-dependent
processors (HeaderFooter, CodeBlock, ContentValue) and builders (Metadata
font stats, OCR, ReadingOrder) since DOCX is a flow-based format with no
page geometry. The simplified chain is: Extract → Chapter → Table → Image →
Formula → LineUnwrap → TextClean → Render.

## 4. Analyze issues and record in backlog

Compare the ParserX output against `expected.md`. Identify and classify
issues:

- **Structural**: heading detection, list formatting, table extraction
- **Content**: missing text, merged/split paragraphs, reading order
- **Formatting**: code blocks, inline code, formula rendering
- **Provider-level**: multi-column layout, image extraction, font parsing

Record findings in `docs/iteration_backlog.md` under **Open Issues**,
following the existing format:

```markdown
- `<doc_name>`: edit_dist=X.XXX, heading_f1=X.XX. Brief description.
  Key issues:
  1. **Issue category**: description and root cause
  2. ...
  Root cause: summary of the dominant problem.
```

## 5. Full regression check

After adding the new document, verify no regressions on existing
documents:

```bash
psx eval ground_truth/
```

Compare per-document metrics against the last recorded baseline in
`docs/iteration_backlog.md`.

## Notes

- `ground_truth/` is gitignored (contains potentially large PDFs/DOCX files).
  Only `expected.md` files may be committed if desired; source documents
  should be shared via other means.
- LlamaParse is used as the baseline generator because it generally
  produces good Markdown from diverse PDF and DOCX types. It is NOT treated
  as ground truth — it is the starting point for manual correction.
- The eval metrics (edit_dist, char_f1, heading_f1, table_cell_f1) are
  computed against `expected.md`. Lower edit_dist and higher F1 are better.
