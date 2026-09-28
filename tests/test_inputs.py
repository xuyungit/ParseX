"""Inputs (IO4, Q119, Q122, Q123): directories with -r, names used twice, images, web addresses."""

import argparse
import functools
import http.server
import threading
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from parserx.console.cli import expand_inputs, output_dir
from parserx.content.convert import image_to_pdf
from parserx.content.fetch import FetchError, fetch


def test_a_directory_with_r_keeps_its_subdirectories(tmp_path):
    for rel in ("a.pdf", "sub/b.docx", "sub/deep/c.png", "sub/notes.txt", ".hidden/d.pdf", "sub/~$e.docx"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    flat = expand_inputs([str(tmp_path)])
    deep = expand_inputs([str(tmp_path)], recursive=True)
    assert [e.path.name for e in flat] == ["a.pdf"]
    assert [(str(e.place), e.path.name) for e in deep] == [(".", "a.pdf"), ("sub", "b.docx"), ("sub/deep", "c.png")]
    assert expand_inputs(["https://example.org/r.pdf"])[0].path is None  # an address, fetched later


def test_a_name_used_twice_gets_a_number(tmp_path):
    args = argparse.Namespace(output=tmp_path / "out")
    taken: set[Path] = set()
    entries = expand_inputs([str(tmp_path / "report.pdf"), str(tmp_path / "report.docx")])
    dirs = [output_dir(e, "report", args, True, taken) for e in entries]
    assert [d.name for d in dirs] == ["report", "report-2"]


def test_an_image_is_a_pdf_of_scanned_pages(tmp_path):
    png = tmp_path / "scan.png"
    Image.new("RGB", (600, 300), "white").save(png, dpi=(300, 300))
    pdf = pymupdf.open(image_to_pdf(png, tmp_path / "out"))
    assert len(pdf) == 1 and round(pdf[0].rect.width) == 144 and not pdf[0].get_text().strip()  # 600 px at 300 dpi
    tiff = tmp_path / "two.tif"
    frames = [Image.new("L", (300, 300), shade) for shade in (255, 128)]
    frames[0].save(tiff, save_all=True, append_images=frames[1:])
    assert len(pymupdf.open(image_to_pdf(tiff, tmp_path / "out"))) == 2  # a frame per page; no dpi: 150


@pytest.fixture
def served(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    doc = pymupdf.open()
    doc.new_page()
    doc.save(root / "paper.pdf")
    (root / "page.html").write_text("<html>not a document</html>")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            if self.path.startswith("/named"):
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.pdf")
            super().end_headers()

        def translate_path(self, path):
            return super().translate_path("/paper.pdf" if path.startswith("/named") else path)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_web_address_is_fetched_and_read_by_its_content(served, tmp_path):
    got = fetch(f"{served}/paper.pdf", tmp_path / "a")
    assert got.name == "paper.pdf" and got.read_bytes().startswith(b"%PDF")
    assert fetch(f"{served}/named?id=7", tmp_path / "b").name == "报告.pdf"  # the server's file name
    with pytest.raises(FetchError, match="not a PDF"):
        fetch(f"{served}/page.html", tmp_path / "c")
    with pytest.raises(FetchError, match="larger than"):
        fetch(f"{served}/paper.pdf", tmp_path / "d", max_mb=0)
    with pytest.raises(FetchError, match="only http"):
        fetch("ftp://example.org/x.pdf", tmp_path / "e")
