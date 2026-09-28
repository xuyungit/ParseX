"""An input given as a web address (Q119, Q123): downloaded into a scratch directory before it is read.

Only http and https.  The download stops at a size limit and a timeout (``input`` in the config).  What the file is
comes from its first bytes, not from the address or the server's content type; a file ParserX cannot read is refused
before it is processed.  The name — for the output directory — is the server's file name (Content-Disposition), else
the last part of the address, else ``download``.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

_MAGIC = ((b"%PDF", ".pdf"), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".doc"), (b"\x89PNG\r\n\x1a\n", ".png"),
          (b"\xff\xd8\xff", ".jpg"), (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"), (b"BM", ".bmp"))


class FetchError(Exception):
    pass


def is_address(text: str) -> bool:
    return bool(re.match(r"^https?://", text, re.IGNORECASE))


def fetch(address: str, scratch: Path, *, max_mb: int = 200, timeout_s: int = 120) -> Path:
    """Download *address* into *scratch*; the file's name ends with the suffix its content calls for."""
    import requests

    if not is_address(address):
        raise FetchError(f"only http and https addresses are read: {address}")
    scratch.mkdir(parents=True, exist_ok=True)
    partial = scratch / "download.part"
    limit = max_mb * (1 << 20)
    try:
        with requests.get(address, stream=True, timeout=timeout_s, headers={"User-Agent": "parserx/0.1"}) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length") or 0) > limit:
                raise FetchError(f"larger than {max_mb} MB (input.max_download_mb)")
            size = 0
            with open(partial, "wb") as out:
                for chunk in response.iter_content(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise FetchError(f"larger than {max_mb} MB (input.max_download_mb)")
                    out.write(chunk)
            name = _name(response.headers.get("content-disposition", ""), address)
    except requests.RequestException as exc:
        raise FetchError(f"download failed: {exc}") from exc
    suffix = kind(partial)
    if suffix is None:
        partial.unlink(missing_ok=True)
        raise FetchError("not a PDF, Word or image file")
    target = scratch / f"{name}{suffix}"
    partial.replace(target)
    return target


def kind(path: Path) -> str | None:
    """The suffix the file's content calls for (.pdf, .docx, .doc, .png, .jpg, .tif, .bmp, .webp), or None."""
    head = path.read_bytes()[:16]
    for magic, suffix in _MAGIC:
        if head.startswith(magic):
            return suffix
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                return ".docx" if "word/document.xml" in archive.namelist() else None
        except zipfile.BadZipFile:
            return None
    return None


def _name(disposition: str, address: str) -> str:
    match = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition) or re.search(r'filename\s*=\s*"?([^";]+)"?',
                                                                                    disposition)
    raw = unquote(match.group(1)) if match else unquote(Path(urlparse(address).path).name)
    stem = Path(raw).stem if raw else ""
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem).strip(" .")
    return stem[:100] or "download"
