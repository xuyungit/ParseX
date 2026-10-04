"""Cut an excerpt of a Word document for test data (parserx/eval/excerpt.py).

    uv run python scripts/docx_excerpt.py SRC.docx OUT.docx 0-109 128 131-134 311:9 …

Parts are body element positions (w:body children from 0): a range, one element, or one element with only its first
n images.  Images nothing refers to any more are removed from the package.
"""

import argparse
import json
from pathlib import Path

from parserx.eval.excerpt import excerpt_docx, parse_parts

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("src", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("parts", nargs="+")
args = ap.parse_args()
print(json.dumps(excerpt_docx(args.src, args.out, parse_parts(args.parts)), ensure_ascii=False))
