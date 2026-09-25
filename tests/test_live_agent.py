"""Real Codex through the hybrid runtime (plan P4-1): skipped unless PARSERX_LIVE_AGENT=1.

Costs a real agent session (about $0.1–0.3 at list price) and service requests.  Validation runs for reports go
through ``scripts/agent_explore.py parse`` instead (snapshot outside the repository, hygiene audit).
"""

import os
import shutil
from pathlib import Path

import pytest

from parserx.config.schema import load_config
from parserx.runtimes.hybrid import parse_document

REPO = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.live_e2e,
              pytest.mark.skipif(os.environ.get("PARSERX_LIVE_AGENT") != "1", reason="set PARSERX_LIVE_AGENT=1")]


def test_codex_reviews_a_document_with_open_items(tmp_path):
    # two pages whose table title and header row are vector drawings: the local reading lists them (Q56)
    source = tmp_path / "doc.pdf"
    shutil.copyfile(REPO / "ground_truth" / "text_table_word" / "input.pdf", source)
    config = load_config(REPO / "configs" / "regression_v2.yaml")
    config.cache.mode = "off"
    outcome = parse_document(source, tmp_path / "out", config, keep_work=True)
    assert outcome.runtime == "hybrid:agent", outcome
    assert outcome.agent.tool_calls > 0 and not outcome.agent.audit
