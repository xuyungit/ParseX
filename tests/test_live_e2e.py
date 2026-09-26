"""Live end-to-end tests of the fixed pipeline that call the real scan engine and VLM from .env."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest
from dotenv import load_dotenv
from PIL import Image, ImageDraw

from parserx.config import load_config
from parserx.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

pytestmark = pytest.mark.live_e2e

_REQUIRED_ENV_VARS = [  # what parserx.yaml reads for the VLM and the scan engine
    "OPENAI_BASE_URL_B",
    "OPENAI_API_KEY_B",
    "PADDLE_OCR_ENDPOINT",
    "PADDLE_OCR_TOKEN",
]


def _live_services_ready() -> bool:
    return all(os.getenv(key) for key in _REQUIRED_ENV_VARS)


def _require_live_services() -> None:
    if not _live_services_ready():
        pytest.skip("Live scan engine / VLM credentials are not configured in .env")


def _load_live_config():
    config = load_config(ROOT / "parserx.yaml")
    config.cache.mode = "off"  # these tests check real requests: a recorded response would be replayed instead
    return config


def _make_text_image(path: Path, *, lines: list[str], size: tuple[int, int]) -> Path:
    image = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(image)

    y = 60
    for line in lines:
        draw.text((60, y), line, fill="black")
        y += 70

    image.save(path, format="PNG")
    return path


def _make_diagram_image(path: Path) -> Path:
    image = Image.new("RGB", (960, 640), color="white")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((80, 80, 360, 220), radius=20, outline="black", width=4)
    draw.rounded_rectangle((560, 80, 840, 220), radius=20, outline="black", width=4)
    draw.rounded_rectangle((320, 360, 620, 520), radius=20, outline="black", width=4)
    draw.line((360, 150, 560, 150), fill="black", width=4)
    draw.line((700, 220, 470, 360), fill="black", width=4)
    draw.text((140, 135), "Input PDF", fill="black")
    draw.text((620, 135), "OCR + LLM", fill="black")
    draw.text((395, 430), "Markdown Output", fill="black")

    image.save(path, format="PNG")
    return path


def _make_pdf_with_fullpage_image(pdf_path: Path, image_path: Path) -> Path:
    image = Image.open(image_path)
    width, height = image.size

    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_image(page.rect, filename=str(image_path))
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def _make_pdf_with_inline_image(pdf_path: Path, image_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=1280)
    page.insert_text(
        (72, 90),
        "System Overview",
        fontsize=22,
        fontname="helv",
    )
    page.insert_text(
        (72, 130),
        "The following figure describes the document parsing pipeline.",
        fontsize=12,
        fontname="helv",
    )
    page.insert_image(pymupdf.Rect(72, 200, 888, 760), filename=str(image_path))
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_live_e2e_ocr_from_scanned_pdf(tmp_path: Path):
    _require_live_services()

    image_path = _make_text_image(
        tmp_path / "ocr_source.png",
        lines=[
            "ParserX OCR E2E",
            "Scanned procurement notice",
            "Amount: 100 units",
        ],
        size=(1400, 900),
    )
    pdf_path = _make_pdf_with_fullpage_image(tmp_path / "ocr_input.pdf", image_path)

    config = _load_live_config()
    config.runtime.describe_figures = False

    result = Pipeline(config).parse_result(pdf_path)

    assert result.api_calls["ocr"] >= 1
    assert result.page_count == 1
    assert len(result.markdown.strip()) > 0


def test_live_e2e_vlm_image_description(tmp_path: Path):
    _require_live_services()

    image_path = _make_diagram_image(tmp_path / "diagram.png")
    pdf_path = _make_pdf_with_inline_image(tmp_path / "vlm_input.pdf", image_path)

    config = _load_live_config()  # the scan engine stays on: the page's picture may send it to the scan engine

    result = Pipeline(config).parse_result(pdf_path)

    assert result.api_calls["vlm"] >= 1
    assert len(result.markdown.strip()) > 0
    assert "[图片]" in result.markdown or "![" in result.markdown
