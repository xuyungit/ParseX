# Test documents

ParserX is evaluated on documents of two kinds (2026-10-04):

- **Public** — published papers, a national industry standard, a patent and pages of the OmniDocBench benchmark
  (`ground_truth/`), synthetic smoke tests (`ground_truth_public/`).  Their annotations (`expected.md`) are in this
  repository; their inputs are not, except the synthetic ones.
- **Private** — company documents, personal files and bid materials.  They are not in this repository: they live in
  a private corpus (`PARSERX_PRIVATE_CORPUS`, default `~/Projects/ParserX-corpus`) and are linked here as
  `ground_truth/<doc>` by `scripts/link_private_corpus.py`; git ignores the links.

Locally the evaluation, the replays and every script see both kinds side by side.  Without the private corpus the
public documents alone are there.

Before a push, `scripts/check_public.py` (installed as the pre-push hook by `scripts/link_private_corpus.py --hook`)
refuses commits that hold a file of a private document, a string of the private corpus's `sensitive.txt`, or a key.
