# ParserX

**High-fidelity document parsing for knowledge bases, RAG, and document analysis.**

English | [中文](README_CN.md)

ParserX converts PDF and DOCX files into well-structured Markdown — preserving chapter hierarchy, tables, images, and formulas — while keeping API costs low through selective, rule-first processing.

## The Problem

Document parsing tools today fall into two camps:

| Approach | Examples | Pros | Cons |
|----------|----------|------|------|
| **Simple extraction** | pdfplumber, PyMuPDF | Fast, cheap, no GPU | Loses table structure, chapter hierarchy, image meaning |
| **Full AI parsing** | LLM-per-page pipelines | High quality | 200+ API calls per doc, slow, expensive, non-deterministic |

ParserX takes a third path: **deterministic rules first, AI only where needed**. The pipeline analyzes font metadata, page geometry, and numbering patterns to handle 70–80% of structure detection without any LLM calls. AI (OCR, VLM) is invoked selectively — only for scanned pages, only for informational images — cutting API costs by 10–20x compared to brute-force approaches.

## How It Compares

| Feature | PyMuPDF | Marker | MinerU | Docling | **ParserX** |
|---------|---------|--------|--------|---------|-------------|
| Chapter/heading detection | - | Heuristic + LLM | Layout model | Layout model | **Font analysis + numbering patterns** (7 CJK/EN patterns, no LLM) |
| Header/footer removal | - | - | Layout model | Layout model | **Geometric + cross-page repetition** (no LLM) |
| Table extraction | Basic | Surya | GPU models | TableFormer | **PyMuPDF native + cross-page merge** |
| OCR for scanned pages | - | Surya (GPU) | PaddlePaddle (GPU) | EasyOCR | **Selective OCR** (only scanned/mixed pages, pluggable backend) |
| Image handling | - | - | - | SmolVLM | **Heuristic classification + selective VLM** (skips 80%+ decorative images) |
| GPU required | No | Yes | Yes | Optional | **No** (uses remote API services) |
| License | AGPL | GPL-3.0 | AGPL | MIT | **MIT** |

### Key Differentiators

- **No GPU required.** Runs on a laptop. OCR and VLM use remote API services (configurable).
- **CJK-first design.** Chinese numbering patterns (第X章, 一/二/三, (一)(二)(三), etc.), CJK space normalization, and bilingual document support are first-class.
- **Measurable quality.** Built-in evaluation framework with text edit distance, heading P/R/F1, table cell F1, and cost tracking. Includes public benchmark support via [OmniDocBench](https://huggingface.co/datasets/opendatalab/OmniDocBench).
- **Minimal, auditable pipeline.** ~35 source files, no deep framework dependency. Each processing step is a standalone module you can read, test, and replace independently.

## Processing Pipeline

```
                    ┌─────────────────────────────────────────────┐
  PDF/DOCX ──────▶  │  Provider  │  Extract text, tables, images  │
                    │            │  with character-level metadata  │
                    └──────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────────────────────────────────────┐
                    │  Builders  │  Font statistics, page types,   │
                    │            │  selective OCR, image extraction │
                    └──────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────────────────────────────────────┐
                    │ Processors │  Header/footer → Chapter →      │
                    │            │  Table → Image → TextClean      │
                    └──────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────────────────────────────────────┐
                    │  Assembly  │  Markdown rendering,            │
                    │            │  chapter splitting              │
                    └─────────────────────────────────────────────┘
```

**Provider** — Extracts raw content from PDF (PyMuPDF character-level) or DOCX (Docling OOXML). Every text span carries font name, size, and bold flag — this metadata drives downstream heading detection without LLM.

**Builders** — Analyzes the extracted content:
- *MetadataBuilder* computes font statistics (body font vs heading candidates) and detects 7 numbering patterns
- *OCRBuilder* classifies each page as native/scanned/mixed, then OCRs only the pages that need it — with text deduplication to avoid double-extracting content on mixed pages
- *ImageExtractor* pulls images from the document, skipping decorative ones

**Processors** — Transforms the annotated document:
- *HeaderFooterProcessor* removes repeated header/footer text using geometric zones + cross-page frequency
- *ChapterProcessor* assigns heading levels from font size ratio + numbering signals, then batch-confirms low-confidence candidates with one LLM fallback call
- *TableProcessor* merges tables that span across page breaks
- *ImageProcessor* classifies images (decorative/informational/chart) and calls VLM for descriptions — only for the images that carry real information
- *TextCleanProcessor* fixes CJK spacing artifacts and encoding issues

**Assembly** — Renders the processed document as Markdown, optionally splitting into chapter files. Figure/table captions are associated before rendering.

## Quick Start

```bash
git clone https://github.com/your-org/ParserX.git
cd ParserX
uv sync
```

### Basic Usage

```bash
# Parse a PDF to stdout
uv run parserx parse document.pdf

# Output to file
uv run parserx parse document.pdf -o output.md

# Split into chapters
uv run parserx parse document.pdf -o output_dir/ --split-chapters

# Use a config file
uv run parserx parse document.pdf -c parserx.yaml -v
```

### Enable VLM Image Descriptions

Set environment variables (or use a `.env` file) for an OpenAI-compatible endpoint:

```bash
OPENAI_BASE_URL="https://api.openai.com/v1" \
OPENAI_API_KEY="your-key" \
uv run parserx parse document.pdf -c parserx.yaml -v
```

Without these, VLM steps are skipped automatically — everything else works.

### Enable OCR for Scanned Documents

```bash
PADDLE_OCR_ENDPOINT="your-paddleocr-endpoint" \
PADDLE_OCR_TOKEN="your-token" \
uv run parserx parse scanned.pdf -c parserx.yaml
```

Without OCR credentials, scanned pages are skipped. See `.env.example` for all available variables.

### Real End-to-End Test

ParserX also includes a live E2E pytest suite that uses `.env` credentials to
call the real online OCR, LLM, and VLM services:

```bash
uv run pytest tests/test_live_e2e.py -q
```

If `.env` contains the required service credentials, `uv run pytest tests/ -q`
will include these live tests automatically.

## Evaluation

ParserX includes a built-in evaluation framework:

```bash
# Evaluate against ground truth
uv run parserx eval ground_truth/ -o report.md

# Quick A/B compare for a feature toggle
uv run parserx compare ground_truth_public \
  --label-a no-fallback \
  --label-b fallback \
  --set-a processors.chapter.llm_fallback=false \
  --set-b processors.chapter.llm_fallback=true

# Download public benchmark (OmniDocBench subset)
uv pip install 'parserx[bench]'
uv run python -m parserx.eval.benchmark --output-dir ground_truth_public
```

Metrics: normalized edit distance, character F1, heading precision/recall/F1, table cell F1, warning count, API calls, and processing cost.

For VLM tuning, you can use `parserx compare` with config overrides such as:
- `--set-a processors.image.vlm_prompt_style=strict_bilingual`
- `--set-b processors.image.vlm_prompt_style=strict_en`
- `--set-a services.vlm.model=model-a`
- `--set-b services.vlm.model=model-b`

Recommended evaluation strategy:
- `ground_truth_public/` includes a tiny checked-in smoke subset for fast regression runs
- larger public benchmarks can be added to the same folder layout
- private ground truth stays outside the repo but uses the same folder layout
- both should be run during local iteration for parser changes

See [Evaluation Guide](docs/evaluation.md) for the public/private benchmark workflow.

## Configuration

All settings in `parserx.yaml`, credentials via environment variables:

```yaml
services:
  vlm:
    endpoint: ${OPENAI_BASE_URL}
    model: ${VLM_MODEL:gpt-5.4-mini}
    api_key: ${OPENAI_API_KEY}

builders:
  ocr:
    engine: paddleocr          # or "none" to disable
    endpoint: ${PADDLE_OCR_ENDPOINT}
    token: ${PADDLE_OCR_TOKEN}
    selective: true             # only OCR scanned/mixed pages

processors:
  header_footer:
    enabled: true
  chapter:
    enabled: true
  image:
    vlm_description: true
    skip_decorative: true
```

See [`parserx.yaml`](parserx.yaml) for the full default configuration.

## Project Status

ParserX is under active development. The core pipeline is functional and tested.

| Component | Status | Notes |
|-----------|--------|-------|
| PDF extraction (PyMuPDF) | ✅ Done | Character-level font metadata |
| DOCX extraction (Docling) | ✅ Done | Style → heading level mapping |
| Header/footer removal | ✅ Done | Geometric + cross-page repetition |
| Chapter/heading detection | ✅ Done | Font ratio + 7 numbering patterns + batch LLM fallback |
| Table extraction + cross-page merge | ✅ Done | Column-count matching + header dedup |
| Selective OCR | ✅ Done | Page classification + text dedup on mixed pages |
| Image classification + VLM | ✅ Done | Heuristic + concurrent VLM calls |
| Text cleaning (CJK) | ✅ Done | Space normalization + encoding fix |
| Evaluation framework | ✅ Done | Edit distance, heading/table F1, OmniDocBench support |
| Line unwrap | 🚧 Planned | Cross-line sentence joining |
| LLM fallback for chapters | ✅ Done | Batch confirmation for low-confidence heading candidates |
| Formula extraction | 🚧 Planned | LaTeX output, requires model integration |
| Hallucination detection | ✅ Done | Cross-validate VLM output against OCR/native text |
| Reading order | 🚧 Planned | Multi-column layout support |

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run tests with real documents (set sample dir)
PARSERX_SAMPLE_DIR=/path/to/test/docs uv run pytest tests/ -v
```

### Project Structure

```
parserx/
├── config/       # YAML config + Pydantic schema
├── models/       # Core data models (PageElement, Document)
├── providers/    # Format extractors (PDF, DOCX)
├── builders/     # Analysis (metadata, OCR, image extraction)
├── processors/   # Transforms (header/footer, chapter, table, image, text)
├── services/     # AI service abstraction (LLM/VLM, OCR)
├── assembly/     # Output (Markdown renderer, chapter splitter)
├── eval/         # Evaluation framework + OmniDocBench benchmark
└── verification/ # Output validation and quality warnings
```

## Documentation

- [Architecture](docs/architecture.md) — Technical design, module details, implementation status
- [Evaluation Guide](docs/evaluation.md) — Public/private benchmark strategy and iteration workflow
- [Requirements](docs/requirements.md) — Background, pain points, industry survey, design goals

## License

MIT
