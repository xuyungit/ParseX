# Public Ground Truth

This directory contains a tiny checked-in evaluation set that can run
without OCR, LLM, or VLM credentials.

- `basic_report/`: headings plus a native PDF table
- `header_footer_cleanup/`: repeated header/footer removal across pages

Each document directory uses the standard layout:

```text
ground_truth_public/
  doc_name/
    input.pdf
    expected.md
    meta.json
```

This set is intentionally small. It is meant to provide a fast,
deterministic regression smoke test in-repo, while larger public and
private corpora can be evaluated with the same `parserx eval` workflow.

Stable subset manifests live under `ground_truth_public/subsets/`.

- `subsets/warning_heavy.txt`: focused slice for OCR/VLM drift work

Example:

```bash
uv run parserx eval ground_truth_public \
  --include-list ground_truth_public/subsets/warning_heavy.txt
```
