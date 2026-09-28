"""The comparison page (plan B5): the tools' Markdown rendered side by side, with the original pages and the
annotation, automatic scores, and manual scores saved to ``manual_scores.json``.

A local server (127.0.0.1): the page needs the tools' images and the rendered original pages, and the manual
scores must land in a file the report can read.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from parserx.tool_eval.adapters import docx_to_pdf
from parserx.tool_eval.runner import DEFAULT_GT_DIRS, DEFAULT_OUT, GROUP_LABELS, find_documents, load_results

PAGE_HTML = Path(__file__).with_name("viewer.html")
PAGE_DPI = 110
# Manual score dimensions, in the order of the user's priorities (information first).
DIMENSIONS = [
    ("info", "信息完整"), ("headings", "标题层级"), ("tables", "表格"), ("readability", "可读性"), ("overall", "总体"),
]
_SOURCE_ORDER = ("parserx-fixed", "parserx-hybrid")
_lock = threading.Lock()


class ManualScores:
    """``{document: {source: {dimension: 1–5, "note": str, "updated": iso}}}`` in one JSON file."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self, document: str, source: str, entry: dict) -> dict:
        keys = {k for k, _ in DIMENSIONS}
        clean = {k: int(v) for k, v in entry.items() if k in keys and v is not None and 1 <= int(v) <= 5}
        note = str(entry.get("note") or "").strip()
        if note:
            clean["note"] = note
        with _lock:
            data = self.load()
            if clean:
                clean["updated"] = datetime.now().isoformat(timespec="seconds")
                data.setdefault(document, {})[source] = clean
            else:
                data.get(document, {}).pop(source, None)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        return clean


def build_index(out_root: Path, gt_dirs) -> dict:
    results = load_results(out_root)
    scores_path = out_root / "scores.json"
    scores = json.loads(scores_path.read_text(encoding="utf-8"))["scores"] if scores_path.exists() else {}
    names = {name for by_doc in results.values() for name in by_doc}
    docs = find_documents(gt_dirs, sorted(names)) if names else []
    sources = sorted(results, key=lambda t: (_SOURCE_ORDER.index(t) if t in _SOURCE_ORDER else len(_SOURCE_ORDER), t))
    labels = {t: next(iter(results[t].values())).get("label", t) for t in sources}
    entries = []
    for doc in sorted(docs, key=lambda d: (d.pages or 99, d.name)):
        per_source = {}
        for t in sources:
            meta = results[t].get(doc.name)
            if meta is None:
                continue
            per_source[t] = {k: meta.get(k) for k in ("status", "error", "wall_time_seconds", "cost_usd",
                                                     "cost_note", "notes")}
            per_source[t]["scores"] = scores.get(t, {}).get(doc.name)
        entries.append({"name": doc.name, "pages": doc.pages, "kind": doc.kind, "group": doc.group,
                        "group_label": GROUP_LABELS[doc.group], "has_expected": doc.expected is not None,
                        "results": per_source})
    return {"sources": [{"id": t, "label": labels[t]} for t in sources],
            "dimensions": [{"key": k, "label": v} for k, v in DIMENSIONS],
            "docs": entries, "manual": ManualScores(out_root / "manual_scores.json").load()}


def make_handler(out_root: Path, gt_dirs):
    out_root = Path(out_root).resolve()
    manual = ManualScores(out_root / "manual_scores.json")
    doc_cache: dict[str, object] = {}

    def document(name: str):
        if name not in doc_cache:
            doc_cache[name] = find_documents(gt_dirs, [name])[0]
        return doc_cache[name]

    def original_pdf(name: str) -> Path:
        doc = document(name)
        if doc.input.suffix.lower() == ".pdf":
            return doc.input
        with _lock:
            return docx_to_pdf(doc.input, out_root / "_pages" / name)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, body: bytes, content_type: str, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=HTTPStatus.OK):
            self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

        def _file(self, base: Path, rel: str):
            target = (base / rel).resolve()
            if not target.is_relative_to(base.resolve()) or not target.is_file():
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".md":
                ctype = "text/markdown; charset=utf-8"
            self._send(target.read_bytes(), ctype)

        def do_GET(self):
            parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/")]
            try:
                if parts == [""]:
                    return self._send(PAGE_HTML.read_bytes(), "text/html; charset=utf-8")
                if parts == ["api", "index"]:
                    return self._json(build_index(out_root, gt_dirs))
                if parts[0] == "src" and len(parts) >= 4:  # /src/<source>/<doc>/<path>
                    source, name, rel = parts[1], parts[2], "/".join(parts[3:])
                    if source == "expected":
                        return self._file(document(name).dir, rel)
                    return self._file(out_root / source / name, rel)
                if parts[:2] == ["api", "pages"] and len(parts) == 3:
                    import pymupdf

                    with pymupdf.open(original_pdf(parts[2])) as pdf:
                        return self._json({"count": pdf.page_count})
                if parts[0] == "page" and len(parts) == 3:  # /page/<doc>/<n>.png, 1-based
                    number = int(parts[2].removesuffix(".png"))
                    cached = out_root / "_pages" / parts[1] / f"p{number:03d}.png"
                    if not cached.exists():
                        import pymupdf

                        with pymupdf.open(original_pdf(parts[1])) as pdf:
                            png = pdf[number - 1].get_pixmap(dpi=PAGE_DPI).tobytes("png")
                        cached.parent.mkdir(parents=True, exist_ok=True)
                        cached.write_bytes(png)
                    return self._send(cached.read_bytes(), "image/png")
            except (KeyError, IndexError, ValueError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if urlparse(self.path).path != "/api/manual":
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                saved = manual.save(str(body["document"]), str(body["source"]), body.get("scores") or {})
            except (KeyError, ValueError, TypeError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return self._json({"saved": saved})

    return Handler


def serve(out_root: Path = DEFAULT_OUT, gt_dirs=DEFAULT_GT_DIRS, port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(out_root, gt_dirs))
    print(f"Comparison page: http://127.0.0.1:{port}/  (results: {out_root}; Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
