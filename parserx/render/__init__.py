"""Output (guide §4.5): the Markdown contract and the sidecar. v1's ``assembly/`` stays untouched."""

from parserx.render.export import IMAGE_DIR, ExportPaths, write_export
from parserx.render.markdown import image_file, render_markdown
from parserx.render.sidecar import export_sidecar, sidecar_json
from parserx.render.summary import document_summary, summary_json

__all__ = ["IMAGE_DIR", "ExportPaths", "document_summary", "export_sidecar", "image_file", "render_markdown",
           "sidecar_json", "summary_json", "write_export"]
