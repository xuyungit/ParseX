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
                               │  local page reading · tables ·     Agent (Codex, or our loop) checks the flagged places
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
  review items, it then hands the document to an agent (Codex CLI, or our own loop), which looks at the flagged places in the page
  images and fixes what the evidence supports.
- **Signals point, the agent judges.** Review items come from properties a correct output must have: what is on the
  page is in the output (checked against a local reading of every page), independent readings agree, the document
  agrees with itself (numbers add up, numbering continues), and the structure agrees with the layout.
- **Headings from agreeing evidence.** A paragraph becomes a title when two independent kinds of evidence agree:
  typography set apart from the body, the layout detector's title label, a section number, or a number that extends
  a title's number. Levels come from the document's own typography and numbering; DOCX headings also come from the
  file's styles and outline levels. No keyword lists.

What it needs:

| | What for | Required |
|---|---|---|
| Scan engine: PaddleOCR-VL (AI Studio jobs API, a token) | scanned pages, the text and tables of images | yes, for scans and images |
| Service model: `gpt-6-luna` through the OpenAI API (a key) | figure descriptions, formula editing, table re-reading | yes |
| Agent: [Codex CLI](https://github.com/openai/codex) on this machine (`codex login`), or our own loop with a model of the config (billed by its API) | the review that gets the most out of a document | recommended |
| LibreOffice (`soffice`) | `.doc` input; drawing the vector images (EMF/WMF) of Word files | for `.doc` |
| Layout model (about 130 MB, fetched once into `~/.cache/parserx/models`) and the local page reading | layout on the CPU; checks against the page image | yes (fetched automatically) |

No GPU is needed. Python 3.13 comes with `uv`.

## Installation

```bash
uv tool install /path/to/parserx-0.1.0-py3-none-any.whl   # or: uv tool install git+<repo URL>
parserx init          # writes ~/.config/parserx/config.yaml and fetches the layout model
$EDITOR ~/.config/parserx/config.yaml                      # the keys you use
parserx check         # every role: configured, reachable, what to do if not
parserx parse report.pdf
```

System tools: LibreOffice (`brew install --cask libreoffice`, `apt install libreoffice`) for `.doc` files; the Codex
CLI (`npm install -g @openai/codex`, then `codex login`) for the agent. Without Codex, ParserX writes the standard
processing's result and says so; `--agent <model>` uses our own loop instead.

For development: `git clone <repo> && cd ParserX && uv sync`, then `uv run parserx …`.

## Usage

```bash
parserx parse report.pdf                       # the standard processing, then the agent where needed → ./output/report/
parserx parse a.pdf b.docx docs/ -o out/       # several files; a directory contributes its documents
parserx parse docs/ -r -o out/                 # subdirectories too; the output keeps their paths
parserx parse https://example.org/report.pdf   # a web address (http, https): fetched, then read
parserx parse scan.jpg                         # an image (JPG, PNG, TIFF, BMP, WebP): read as a scanned page
parserx parse report.pdf --no-agent            # the standard processing only: deterministic
parserx parse report.pdf --agent deepseek-flash  # our own loop with a model of the config instead of Codex
parserx parse report.pdf --vlm glm-5.3-flashx  # another service model
parserx parse report.pdf --stdout              # Markdown to stdout
parserx parse report.pdf --json                # the result summary as JSON on stdout (progress on stderr)
parserx parse report.pdf --lang en             # English console and English notes in the Markdown (default: Chinese)
parserx parse report.pdf --report --sidecar    # also the summary JSON and the block-level record
parserx parse report.pdf --no-ocr              # without the scan engine: scanned pages stay unrecognised (partial)
parserx parse report.pdf --no-vlm              # without the service model: figures are not described
parserx parse report.pdf --set runtime.formulas=false   # any config value
```

Ctrl-C keeps the work directory; running the same command again continues where it stopped. Before the first
document, `parse` says which service cannot work as configured.

**Output** (`./output/<name>/`): the Markdown and the images it links.

| File | Content |
|---|---|
| `<name>.md` | The Markdown: headings, paragraphs, tables (GFM, or HTML for merged cells), images each with a short note, formulas as LaTeX, page anchors (`<!-- PAGE n -->`); where something could not be read, a line says so (`> 〔未识别〕第 3 页：…`) |
| `images/` | The images the Markdown links |
| `<name>.json` (`--report`) | Summary: status (`complete` / `partial` / `failed`), outline, tables, images, what is missing and why, open review items, processing and cost |
| `<name>.blocks.json` (`--sidecar`) | The block-level record: every block with its sources, decisions and the accounting ledger (development and audit) |

A second document with the same name in the same place goes to `<name>-2/`.

Exit code 0 when every document was written (complete or partial), 1 when one failed, 130 on Ctrl-C.

## Configuration

Layers, each merged over the one before: the built-in defaults
([`parserx/config/defaults.yaml`](parserx/config/defaults.yaml): the production settings and the known models) →
`./parserx.yaml` (a project's own settings, optional) → the personal `~/.config/parserx/config.yaml` (keys, your
endpoints, which model does what; `PARSERX_CONFIG_DIR` moves it) → `--config FILE`. `${VAR}` reads an environment
variable; no `.env` file is read.

A personal config:

```yaml
models:                     # the built-in entries know endpoints and parameters: add the key
  gpt-6-luna: {api_key: sk-...}
  gpt-6-sol: {api_key: sk-...}          # for --agent gpt-6-sol (our own loop)
  deepseek-flash: {api_key: sk-...}
builders:
  ocr: {token: ...}                     # the scan engine, from https://aistudio.baidu.com/paddleocr
services:
  vlm: {use: gpt-6-luna}                # the service model (default)
runtime:
  mode: hybrid                          # or fixed: no agent
  agent: {engine: codex}                # or {engine: loop, use: deepseek-flash}
```

A model entry says how to talk to the model: endpoint, `api_style` (responses / chat), whether it takes a
temperature, the reasoning efforts it accepts (`efforts`), the strongest structured output it honours
(`structured_output`). `parserx check --model NAME` probes a model and compares it with its entry. Keys of earlier
versions (`processors`, `providers`, `pipeline` …) are ignored; `parserx init` keeps an old personal config as
`config.yaml.v1.bak` and carries the values of an old `~/.config/parserx/.env` over.

## Evaluation

```bash
uv run parserx check                                     # scan engine, service model, agent reachable
uv run pytest -q --ignore=tests/test_live_e2e.py         # L0: offline unit and contract tests
uv run python scripts/regression_test.py --core --repeat 2   # L1: core documents, offline replay, twice
uv run python scripts/regression_test.py --replay eval_runs/<run>   # replay a frozen run
uv run parserx dev eval ground_truth/ -o report.md        # scores against ground truth
```

Metrics: character F1 (order-aware edit distance too), table cell F1 (position and merged cells), heading F1
(text and level) and role F1 (text only), key-content errors, real requests and cost. Frozen runs
(`eval_runs/`) record outputs, responses and the environment so that comparisons are reproducible offline.
`parserx dev tool-eval` compares other parsers on the same ground truth (install the `bench` extra).

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
├── tools/       # the toolkit and its JSON CLI: the agent's four tools read_draft, view_source, edit_draft, submit_draft, and run_pipeline, which makes the first draft
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
