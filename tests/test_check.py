"""`parserx check` and the layout model file (release R3, R5), offline."""

import hashlib
from pathlib import Path

import pytest

from parserx.check import check, report
from parserx.config.schema import ParserXConfig, apply_overrides, load_config_with_result


def _config(tmp_path, monkeypatch, *overrides):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("parserx.check.shutil.which", lambda name: None)  # no Codex, no LibreOffice here
    loaded = load_config_with_result()
    return apply_overrides(loaded.config, [f"layout.model_dir={tmp_path / 'models'}", *overrides]), loaded


def test_check_says_what_is_missing_and_how_to_add_it(tmp_path, monkeypatch):
    config, loaded = _config(tmp_path, monkeypatch)
    text, code = report(check(config, loaded, offline=True), loaded)
    assert code == 1  # the scan engine and the service model are required
    assert "models.glm-5.3-flashx.api_key" in text and "models.qwen3.8-flash.api_key" in text
    assert "codex login" in text  # the agent: Codex, not found here
    loop = apply_overrides(config, ["runtime.agent.engine=loop"])
    assert "models.deepseek-flash.api_key" in report(check(loop, loaded, offline=True), loaded)[0]
    assert "parserx init" in text  # no personal config yet


def test_check_passes_when_the_required_roles_are_configured(tmp_path, monkeypatch):
    config, loaded = _config(tmp_path, monkeypatch, "builders.ocr.glm.api_key=g", "services.vlm.api_key=k",
                             "runtime.mode=fixed")
    items = check(config, loaded, offline=True)
    text, code = report(items, loaded)
    assert code == 0 and [i.ok for i in items][:3] == [True, True, None]  # agent off: not a failure


def test_the_layout_model_is_checked_before_it_is_kept(tmp_path, monkeypatch):
    from parserx.layout import detector

    body = b"onnx-bytes"
    monkeypatch.setattr(detector, "model_source", lambda model: ("https://example/model.onnx",
                                                                 hashlib.sha256(body).hexdigest()))

    class Response:
        headers = {"content-length": str(len(body))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield body

    monkeypatch.setattr("requests.get", lambda url, **kw: Response())
    layout = ParserXConfig().layout.model_copy(update={"model_dir": str(tmp_path)})
    seen = []
    path = detector.ensure_model(layout, lambda done, total: seen.append((done, total)))
    assert path.read_bytes() == body and seen == [(len(body), len(body))]
    monkeypatch.setattr(detector, "model_source", lambda model: ("https://example/model.onnx", "0" * 64))
    other = layout.model_copy(update={"model": "other"})
    with pytest.raises(RuntimeError, match="SHA-256"):
        detector.ensure_model(other)
    assert not list(Path(tmp_path).glob("other*"))  # nothing half-written is left
