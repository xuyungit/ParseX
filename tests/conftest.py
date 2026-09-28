"""Every test sees an empty personal config directory: the developer's own ~/.config/parserx (keys, model choices)
never leaks into a test (Q107)."""

import pytest


@pytest.fixture(autouse=True)
def _no_personal_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("PARSERX_CONFIG_DIR", str(tmp_path_factory.mktemp("personal")))


@pytest.fixture(autouse=True)
def _no_model_download(monkeypatch):
    """Unit tests are offline: parse does not fetch the layout model (tests use a fake detector)."""
    monkeypatch.setattr("parserx.check.fetch_layout_model", lambda *a, **k: None)
