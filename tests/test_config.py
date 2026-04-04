"""Tests for configuration loading."""

from pathlib import Path

from parserx.config import ParserXConfig, load_config


def test_default_config():
    config = ParserXConfig()
    assert config.providers.pdf.engine == "pymupdf"
    assert config.processors.text_clean.enabled is True
    assert config.processors.formula.enabled is False


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
