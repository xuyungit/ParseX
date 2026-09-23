"""Tests for configuration loading."""

from pathlib import Path

from parserx.config import ParserXConfig, load_config, load_config_with_result


def test_default_config():
    config = ParserXConfig()
    assert config.providers.pdf.engine == "pymupdf"
    assert config.processors.text_clean.enabled is True
    assert config.processors.formula.enabled is True
    assert config.processors.formula.vlm_correction is False
    assert config.processors.image.vlm_prompt_style == "strict_auto"
    assert config.processors.image.vlm_structured_output_mode == "json_schema"
    assert config.processors.image.vlm_max_tokens == 8192
    assert config.processors.image.vlm_skip_large_text_overlap_chars == 1200
    assert config.services.vlm.api_style == "auto"
    assert config.services.vlm.extra_body == {}


def test_load_config_from_yaml(tmp_path: Path):
    config_file = tmp_path / "test.yaml"
    config_file.write_text("""
processors:
  formula:
    enabled: true
  text_clean:
    fix_cjk_spaces: false
""")
    config = load_config(config_file)
    assert config.processors.formula.enabled is True
    assert config.processors.text_clean.fix_cjk_spaces is False
    # Defaults preserved
    assert config.providers.pdf.engine == "pymupdf"


def test_load_config_missing_file():
    config = load_config("/nonexistent/path.yaml")
    assert config.providers.pdf.engine == "pymupdf"


def test_load_config_uses_project_yaml_by_default(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "parserx.yaml"
    config_file.write_text("""
processors:
  chapter:
    llm_fallback: false
builders:
  ocr:
    engine: none
""")
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.processors.chapter.llm_fallback is False
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
    assert loaded.config.providers.pdf.engine == "pymupdf"


def test_env_var_resolution(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "my-secret-key")
    config_file = tmp_path / "test.yaml"
    config_file.write_text("""
services:
  llm:
    api_key: ${TEST_API_KEY}
    model: ${MISSING_VAR:fallback-model}
""")
    config = load_config(config_file)
    assert config.services.llm.api_key == "my-secret-key"
    assert config.services.llm.model == "fallback-model"


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
processors:
  image:
    vlm_prompt_style: strict_auto
""", encoding="utf-8")

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("""
extends: base.yaml
services:
  vlm:
    model: alt-model
processors:
  image:
    vlm_prompt_style: strict_en
""", encoding="utf-8")

    config = load_config(overlay)

    assert config.services.vlm.endpoint == "https://example.invalid/openai"
    assert config.services.vlm.model == "alt-model"
    assert config.services.vlm.api_key == "overlay-secret"
    assert config.processors.image.vlm_prompt_style == "strict_en"


def test_load_config_extends_resolves_relative_paths(tmp_path: Path):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    base = tmp_path / "parserx.yaml"
    base.write_text("services:\n  llm:\n    model: base-llm\n", encoding="utf-8")
    overlay = configs_dir / "exp.yaml"
    overlay.write_text("extends: ../parserx.yaml\nservices:\n  llm:\n    model: exp-llm\n", encoding="utf-8")

    config = load_config(overlay)

    assert config.services.llm.model == "exp-llm"


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


def test_regression_config_turns_off_every_llm_call_and_keeps_the_rest():
    from parserx.config.schema import load_config

    base = load_config(_REPO / "parserx.yaml")
    reg = load_config(_REPO / "configs" / "regression.yaml")

    assert reg.builders.quality_check.enabled is False
    assert reg.processors.chapter.llm_fallback is False
    assert reg.processors.line_unwrap.llm_fallback is False
    assert reg.processors.content_value.llm_fallback is False

    for cfg in (base, reg):
        cfg.builders.quality_check.enabled = False
        for name in ("chapter", "line_unwrap", "content_value", "header_footer"):
            getattr(cfg.processors, name).llm_fallback = False
    assert reg == base


def test_config_fingerprint_ignores_secrets_but_not_settings():
    from parserx.config.schema import apply_overrides
    from parserx.eval.reporting import config_fingerprint

    base = ParserXConfig()
    secret = apply_overrides(base, ["services.llm.api_key=sk-other", "builders.ocr.token=t"])
    changed = apply_overrides(base, ["services.llm.model=other-model"])

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
