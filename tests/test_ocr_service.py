"""Tests for OCR service helpers."""

from parserx.services.ocr import _extract_bbox


def test_extract_bbox_from_dict():
    bbox = _extract_bbox({"bbox": {"left": 1, "top": 2, "right": 3, "bottom": 4}})
    assert bbox == (1.0, 2.0, 3.0, 4.0)


def test_extract_bbox_from_flat_list():
    bbox = _extract_bbox({"block_bbox": [1, 2, 3, 4]})
    assert bbox == (1.0, 2.0, 3.0, 4.0)


def test_extract_bbox_from_polygon_points():
    bbox = _extract_bbox({"coordinate": [[1, 2], [3, 2], [3, 4], [1, 4]]})
    assert bbox == (1.0, 2.0, 3.0, 4.0)


def test_extract_bbox_from_flat_polygon():
    bbox = _extract_bbox({"coordinate": [1, 2, 3, 2, 3, 4, 1, 4]})
    assert bbox == (1.0, 2.0, 3.0, 4.0)


def test_extract_bbox_fallbacks_to_zero():
    bbox = _extract_bbox({"coordinate": "invalid"})
    assert bbox == (0.0, 0.0, 0.0, 0.0)
