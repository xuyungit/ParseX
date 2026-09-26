# ParserX

**Document parsing for LLM use: PDF and DOCX (including scans and embedded images) to faithful Markdown.**

English | [中文](README_CN.md)

ParserX turns PDF, DOCX and DOC files into Markdown that a language model can use, together with a machine-readable
record of where every piece of content went. It is built for Chinese and mixed Chinese/English documents: reports,
standards, contracts, papers, scanned forms.

Priorities, in order: **nothing on the page is lost**, then the **heading hierarchy**, then benchmark scores.

## How it works

```
document ─▶ workspace ─▶ standard processing (fixed pipeline) ─▶ open review items? ──no──▶ export
                               │                                        │ yes
                               │  native text · scan engine · layout    ▼
                               │  local page reading · tables ·     Agent (Codex) checks the flagged places
                               │  figures · formulas · titles        with the same tools, sees the page images
                               ▼                                        │
                        accounting check: every discovered item  ◀──────┘
                        is output, merged, a duplicate, excluded with a reason, or a recorded failure
```

- **Document workspace.** Pages, blocks, their sources (native text layer, OOXML, scan engine, VLM), decisions and
  the ledger of discovered content live in a workspace, not in a model's context.
- **Toolkit with program-enforced constraints.** Tools read, recognise, review tables, describe figures and change
  structure. A model's change is only a candidate: it is accepted only when the program can verify it. For example,
  numbers may not change against the evidence, structure changes never rewrite text, and the outline stays legal.
  Content that disappears without a recorded reason is a defect, and the accounting check catches it on every run.
- **Two runtimes over the same tools.** The fixed pipeline runs the standard processing in a fixed order: it is
  deterministic and fast. The **hybrid** runtime (the default) first runs the fixed pipeline. When there are open
  review items, it then hands the document to an agent (Codex CLI), which looks at the flagged places in the page
  images and fixes what the evidence supports.
- **Signals point, the agent judges.** Review items come from properties a correct output must have: what is on the
  page is in the output (checked against a local reading of every page), independent readings agree, the document
  agrees with itself (numbers add up, numbering continues), and the structure agrees with the layout.
- **Headings from agreeing evidence.** A paragraph becomes a title when two independent kinds of evidence agree:
  typography set apart from the body, the layout detector's title label, a section number, or a number that extends
  a title's number. Levels come from the document's own typography and numbering; DOCX headings also come from the
  file's styles and outline levels. No keyword lists.

Services: a remote scan engine (PaddleOCR-VL, AI Studio jobs API) for scanned pages and images with text; a VLM
(OpenAI, `gpt-6-luna` by default) for figure descriptions and reviews; local CPU models for layout detection and
the page reading. No GPU is needed.

## Installation

```bash
# as a command-line tool
uv tool install -e /path/to/ParserX
parserx init                 # writes ~/.config/parserx/config.yaml and .env
vim ~/.config/parserx/.env   # service credentials

# for development
git clone <repo> && cd ParserX && uv sync
uv run parserx --help
```

`psx` is a short alias of `parserx`. Credentials (`~/.config/parserx/.env` or `./.env`):

```bash
OPENAI_API_KEY=...                      # VLM
PADDLE_OCR_ENDPOINT=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLE_OCR_TOKEN=...                    # scan engine, from https://aistudio.baidu.com/paddleocr
```

The hybrid runtime needs the [Codex CLI](https://github.com/openai/codex), installed and logged in (`codex login`).
When it is missing or not logged in, ParserX uses the fixed pipeline's result and says why.

## Usage

```bash
parserx parse report.pdf                       # hybrid (default) → ./output/report/
parserx parse a.pdf b.docx docs/ -o out/       # several files; a directory contributes its PDF/DOCX/DOC
parserx parse report.pdf --runtime fixed       # fixed pipeline only: deterministic, no agent
parserx parse report.pdf --stdout              # Markdown to stdout
parserx parse report.pdf --json                # the result summary as JSON on stdout (progress on stderr)
parserx parse report.pdf --lang en             # console in English (default: Chinese)
parserx parse report.pdf --no-ocr              # without the scan engine: scanned pages stay unrecognised (partial)
parserx parse report.pdf --no-vlm              # without the VLM: figures are not described
parserx parse report.pdf --set runtime.formulas=false   # any config value
```

Ctrl-C keeps the work directory; running the same command again continues where it stopped.

**Output** (`./output/<name>/`):

| File | Content |
|---|---|
| `<name>.md` | The Markdown: headings, paragraphs, tables (GFM, or HTML for merged cells), images with descriptions, formulas as LaTeX, page anchors |
| `<name>.json` | Summary: status (`complete` / `partial` / `failed`), outline, tables, images, what is missing and why, open review items, processing and cost |
| `<name>.blocks.json` | The sidecar: every block with its sources, decisions and the accounting ledger |
| `images/` | Images referenced from the Markdown |

Exit code 0 when every document was written (complete or partial), 1 when one failed, 130 on Ctrl-C.

## Configuration

Resolved in order: `--config` > `./parserx.yaml` > `~/.config/parserx/config.yaml` > defaults. The main settings:

```yaml
builders:
  ocr:                    # the scan engine
    engine: paddleocr     # "none" = --no-ocr
    endpoint: ${PADDLE_OCR_ENDPOINT}
    token: ${PADDLE_OCR_TOKEN}
services:
  vlm:
    endpoint: ${OPENAI_BASE_URL:https://api.openai.com/v1}
    model: ${VLM_MODEL:gpt-6-luna}
    api_key: ${OPENAI_API_KEY}
runtime:
  mode: hybrid            # or fixed
  agent: {model: gpt-6-sol, effort: medium}
cache:
  mode: read_write        # responses are recorded and replayed (off / read_only / refresh)
scheduling:
  budget: {}              # per-document limits on requests, cost, time
```

See [`parserx.yaml`](parserx.yaml) for the full production configuration. Keys from earlier versions (`processors`,
`providers`, `pipeline` …) are ignored.

## Evaluation

```bash
uv run python scripts/check_services.py                  # scan engine and VLM reachable
uv run pytest -q --ignore=tests/test_live_e2e.py         # L0: offline unit and contract tests
uv run python scripts/regression_test.py --core --repeat 2   # L1: core documents, offline replay, twice
uv run python scripts/regression_test.py --replay eval_runs/<run>   # replay a frozen run
uv run parserx eval ground_truth/ -o report.md            # scores against ground truth
```

Metrics: character F1 (order-aware edit distance too), table cell F1 (position and merged cells), heading F1
(text and level) and role F1 (text only), key-content errors, real requests and cost. Frozen runs
(`eval_runs/`) record outputs, responses and the environment so that comparisons are reproducible offline.
`parserx tool-eval` compares other parsers on the same ground truth (install the `bench` extra).

## Project structure

```
parserx/
├── ir/          # data model: Block, SourceAnchor, Observation, Relation, Asset, Decision
├── workspace/   # the document workspace: state, queries, integrity
├── content/     # native PDF, DOCX (OOXML), scan engine, selection, page furniture, reading order
├── layout/      # local layout detector          routing/   # image routing
├── reading/     # local page reading and two-way comparison with the output
├── tables/      # TableGrid, GFM/HTML, cross-page merge, arithmetic checks
├── hierarchy/   # titles and levels, legality of structure changes
├── accounting/  # the accounting check
├── tools/       # the toolkit and its JSON CLI (process, read, recognize, review_table, describe_figure, …)
├── runtimes/    # fixed pipeline, hybrid, Codex adapter
├── console/     # the parse command's progress and summary (zh / en)
├── render/      # Markdown, sidecar, summary
├── scheduling/  # service gateway: budgets, retries, ordered concurrency, cost
├── cache/       # response cache            services/  # scan engine and VLM clients
├── skills/      # task guides for the agent  prompts/   # VLM task prompts
└── eval/        # metrics, frozen runs, regression gates, OmniDocBench conversion
```

## Documentation

- [Design and development guide](docs/redesign_guide.md): architecture, decisions (§14), phases and status (§12).
- [Evaluation](docs/evaluation.md): metric definitions.
- [Requirements](docs/requirements.md): background and pain points.
- The v1 pipeline (rule-based processors, until 2026-09) is described in [docs/architecture.md](docs/architecture.md)
  and kept in the local git tag `v1-final`.

## License

MIT
