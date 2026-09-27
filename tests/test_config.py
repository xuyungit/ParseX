"""Tests for configuration loading."""

from pathlib import Path

from parserx.config import ParserXConfig, load_config, load_config_with_result


def test_default_config():
    config = ParserXConfig()
    assert config.builders.ocr.engine == "paddleocr" and config.builders.ocr.model == "PaddleOCR-VL-1.6"
    assert config.runtime.mode == "hybrid" and config.runtime.describe_figures and config.runtime.formulas
    assert config.services.vlm.api_style == "auto"
    assert config.services.vlm.extra_body == {}


def test_load_config_from_yaml(tmp_path: Path):
    config_file = tmp_path / "test.yaml"
    config_file.write_text("""
runtime:
  formulas: false
processors:            # a section of earlier versions: ignored
  text_clean:
    fix_cjk_spaces: false
""")
    config = load_config(config_file)
    assert config.runtime.formulas is False
    # Defaults preserved
    assert config.runtime.describe_figures is True and config.builders.ocr.engine == "paddleocr"


def test_load_config_missing_file():
    config = load_config("/nonexistent/path.yaml")
    assert config.builders.ocr.engine == "paddleocr"


def test_load_config_uses_project_yaml_by_default(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "parserx.yaml"
    config_file.write_text("""
runtime:
  page_reading: false
builders:
  ocr:
    engine: none
""")
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.runtime.page_reading is False
    assert config.builders.ocr.engine == "none"


def test_load_config_with_result_reports_project_source(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "parserx.yaml"
    config_file.write_text("builders:\n  ocr:\n    engine: none\n")
    monkeypatch.chdir(tmp_path)

    loaded = load_config_with_result()

    assert loaded.source == "project"
    assert loaded.resolved_path == config_file
    assert loaded.config.builders.ocr.engine == "none"


def test_load_config_with_result_reports_default_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import parserx.config.schema as _schema
    monkeypatch.setattr(_schema, "_GLOBAL_CONFIG_DIR", tmp_path / "no_global")

    loaded = load_config_with_result()

    assert loaded.source == "defaults"
    assert loaded.resolved_path is None
    assert loaded.config.builders.ocr.engine == "paddleocr"


def test_env_var_resolution(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "my-secret-key")
    config_file = tmp_path / "test.yaml"
    config_file.write_text("""
services:
  vlm:
    api_key: ${TEST_API_KEY}
    model: ${MISSING_VAR:fallback-model}
""")
    config = load_config(config_file)
    assert config.services.vlm.api_key == "my-secret-key"
    assert config.services.vlm.model == "fallback-model"


def test_load_config_supports_extends_overlay(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/openai")
    monkeypatch.setenv("OPENAI_API_KEY", "overlay-secret")

    base = tmp_path / "base.yaml"
    base.write_text("""
services:
  vlm:
    endpoint: ${OPENAI_BASE_URL}
    model: base-model
    api_key: ${OPENAI_API_KEY}
runtime:
  formulas: true
""", encoding="utf-8")

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("""
extends: base.yaml
services:
  vlm:
    model: alt-model
runtime:
  formulas: false
""", encoding="utf-8")

    config = load_config(overlay)

    assert config.services.vlm.endpoint == "https://example.invalid/openai"
    assert config.services.vlm.model == "alt-model"
    assert config.services.vlm.api_key == "overlay-secret"
    assert config.runtime.formulas is False


def test_load_config_extends_resolves_relative_paths(tmp_path: Path):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    base = tmp_path / "parserx.yaml"
    base.write_text("services:\n  vlm:\n    model: base-vlm\n", encoding="utf-8")
    overlay = configs_dir / "exp.yaml"
    overlay.write_text("extends: ../parserx.yaml\nservices:\n  vlm:\n    model: exp-vlm\n", encoding="utf-8")

    config = load_config(overlay)

    assert config.services.vlm.model == "exp-vlm"


def test_load_config_supports_service_api_style_and_extra_body(tmp_path: Path):
    config_file = tmp_path / "test.yaml"
    config_file.write_text("""
services:
  vlm:
    endpoint: https://example.invalid/v1
    model: qwen3.6-plus
    api_style: responses
    extra_body:
      enable_thinking: false
      custom_flag: demo
""", encoding="utf-8")

    config = load_config(config_file)

    assert config.services.vlm.api_style == "responses"
    assert config.services.vlm.extra_body == {
        "enable_thinking": False,
        "custom_flag": "demo",
    }


# ── Regression config (guide §12 P0-2) ──────────────────────────────────

_REPO = Path(__file__).resolve().parent.parent


def test_regression_config_is_the_production_config():
    # Phase 5: the LLM switches it turned off belonged to v1; what processes a document is the same
    from parserx.config.schema import load_config
    from parserx.eval.reporting import config_fingerprint

    base = load_config(_REPO / "parserx.yaml")
    reg = load_config(_REPO / "configs" / "regression.yaml")
    assert config_fingerprint(reg) == config_fingerprint(base)


def test_config_fingerprint_ignores_secrets_but_not_settings():
    from parserx.config.schema import apply_overrides
    from parserx.eval.reporting import config_fingerprint

    base = ParserXConfig()
    secret = apply_overrides(base, ["services.vlm.api_key=sk-other", "builders.ocr.token=t"])
    changed = apply_overrides(base, ["services.vlm.model=other-model"])

    assert config_fingerprint(secret) == config_fingerprint(base)
    replay = apply_overrides(base, ["cache.mode=read_only"])
    assert config_fingerprint(replay) == config_fingerprint(base)  # cache replays, it does not change processing
    assert config_fingerprint(changed) != config_fingerprint(base)


def test_fingerprint_ignores_transport_and_prices_but_not_budget():
    from parserx.config.schema import PriceConfig, apply_overrides
    from parserx.eval.reporting import config_fingerprint

    base = ParserXConfig()
    transport = apply_overrides(base, ["scheduling.retry.max_attempts=5"])
    transport.scheduling.prices = {"m": PriceConfig(input=1.0, output=2.0)}
    budget = apply_overrides(base, ["scheduling.budget.usd=0.5"])
    assert config_fingerprint(transport) == config_fingerprint(base)
    assert config_fingerprint(budget) != config_fingerprint(base)  # an exhausted budget changes outputs


def test_fields_removed_from_the_config_do_not_change_a_frozen_runs_fingerprint():
    # Phase 5: the pipeline switch and the v1 sections left the schema; a run frozen with them still replays
    from parserx.eval.reporting import config_fingerprint, resolved_fingerprint

    resolved = ParserXConfig().model_dump(mode="json")
    resolved["pipeline"] = "v2"
    resolved["retired_section"] = {"llm_fallback": False}  # stands for a section a later phase removes
    assert resolved_fingerprint(resolved) == config_fingerprint(ParserXConfig())
    assert ParserXConfig().runtime.mode == "hybrid"  # Q13


def test_init_template_has_the_production_settings(tmp_path, monkeypatch):
    # the global config written by `parserx init` processes like the project's parserx.yaml; only the credential
    # variable names and the cache directory differ
    from parserx.cli import _cmd_init, config_template
    from parserx.config.schema import load_config

    for name, value in {"OPENAI_BASE_URL_B": "https://e", "OPENAI_BASE_URL": "https://e", "OPENAI_API_KEY_B": "k",
                        "OPENAI_API_KEY": "k", "PADDLE_OCR_ENDPOINT": "https://o", "PADDLE_OCR_TOKEN": "t"}.items():
        monkeypatch.setenv(name, value)
    for name in ("VLM_MODEL", "LLM_MODEL", "VLM_MODEL_B", "LLM_MODEL_B", "PADDLE_OCR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.env (the repository's sets other model names)
    monkeypatch.setattr("parserx.config.schema._GLOBAL_CONFIG_DIR", tmp_path / "no-global")
    (tmp_path / "global.yaml").write_text(config_template(tmp_path / "cache"), encoding="utf-8")
    ours, project = load_config(tmp_path / "global.yaml"), load_config(_REPO / "parserx.yaml")
    assert ours.cache.dir == str(tmp_path / "cache")
    ours.cache.dir = project.cache.dir
    assert ours == project

    config_dir = tmp_path / "parserx"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("old: true\n")
    (config_dir / ".env").write_text("OPENAI_API_KEY=mine\n")
    _cmd_init(force=True, config_dir=config_dir)
    assert (config_dir / "config.yaml.bak").read_text() == "old: true\n"
    assert "gpt-6-sol" in (config_dir / "config.yaml").read_text()
    assert (config_dir / ".env").read_text() == "OPENAI_API_KEY=mine\n"


# ── Model entries (Q100) ────────────────────────────────────────────────

_MODELS = {"luna": {"endpoint": "https://a/v1", "model": "luna-x", "send_temperature": False, "min_output_tokens": 1024},
           "glm": {"endpoint": "https://g/v4", "model": "glm-x", "api_style": "chat", "efforts": ["low", "high", "max"],
                   "structured_output": "json_object"}}


def test_a_place_that_names_a_model_takes_its_entry_and_keeps_what_it_writes():
    config = ParserXConfig.model_validate({
        "models": _MODELS,
        "services": {"vlm": {"use": "glm", "reasoning_effort": "none", "max_concurrent": 3, "endpoint": "https://mine"}},
        "runtime": {"agent": {"engine": "loop", "use": "glm", "effort": "medium"}}})
    vlm, agent = config.services.vlm, config.runtime.agent
    assert (vlm.model, vlm.api_style, vlm.efforts, vlm.structured_output) == ("glm-x", "chat", ["low", "high", "max"],
                                                                               "json_object")
    assert vlm.endpoint == "https://mine" and vlm.max_concurrent == 3  # written at the place: it wins
    assert (agent.model, agent.api, agent.endpoint, agent.efforts) == ("glm-x", "chat", "https://g/v4",
                                                                       ["low", "high", "max"])
    luna = ParserXConfig.model_validate({"models": _MODELS, "runtime": {"agent": {"use": "luna"}}}).runtime.agent
    assert luna.api == "responses"  # the entry's "auto" leaves the loop's own API


def test_an_unknown_model_name_is_an_error():
    import pytest

    with pytest.raises(ValueError, match="no model 'nope'"):
        ParserXConfig.model_validate({"models": _MODELS, "services": {"vlm": {"use": "nope"}}})


def test_changing_the_model_on_the_command_line_takes_the_new_entry():
    from parserx.config.schema import apply_overrides

    base = ParserXConfig.model_validate({"models": _MODELS, "services": {"vlm": {"use": "luna", "max_concurrent": 3}}})
    moved = apply_overrides(base, ["services.vlm.use=glm"]).services.vlm
    assert (moved.model, moved.endpoint, moved.send_temperature, moved.min_output_tokens) == ("glm-x", "https://g/v4",
                                                                                                None, 0)
    assert moved.max_concurrent == 3  # not the entry's: kept


def test_naming_a_model_does_not_change_the_fingerprint():
    # the production config now names its model; a frozen run that wrote the same settings in place still replays
    from parserx.eval.reporting import config_fingerprint

    named = ParserXConfig.model_validate({"models": _MODELS, "services": {"vlm": {"use": "luna", "reasoning_effort": "none"}}})
    inline = ParserXConfig.model_validate({"services": {"vlm": {**{k: v for k, v in _MODELS["luna"].items()},
                                                                "reasoning_effort": "none"}}})
    assert config_fingerprint(named) == config_fingerprint(inline)


def test_an_effort_is_sent_as_the_nearest_the_model_accepts():
    from parserx.config.schema import effort_for

    glm = ["low", "high", "max"]
    assert effort_for("low", glm) == "low" and effort_for("none", glm) == "low"
    assert effort_for("medium", glm) == "low"  # a tie: services are economy first (Q40)
    assert effort_for("medium", glm, higher=True) == "high"  # the agent is capability first (Q103)
    assert effort_for("xhigh", glm) == "high"  # between high and max: a tie, the lower
    assert effort_for("none", None) == "none" and effort_for(None, glm) is None  # not listed / nothing asked
    assert effort_for("medium", []) is None  # a model that takes no effort
