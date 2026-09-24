"""Output (guide §4.5): the Markdown contract and the sidecar. v1's ``assembly/`` stays untouched."""

from parserx.render.export import IMAGE_DIR, write_export
from parserx.render.markdown import image_file, render_markdown
from parserx.render.sidecar import export_sidecar, sidecar_json

__all__ = ["IMAGE_DIR", "export_sidecar", "image_file", "render_markdown", "sidecar_json", "write_export"]
