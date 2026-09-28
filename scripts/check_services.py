#!/usr/bin/env python3
"""The same as `parserx check` (release R3): every role configured and reachable; --model probes one model entry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parserx.check import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
