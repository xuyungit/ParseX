"""Skills (guide §5.4, plan P1-8): present, marked as drafts, content-hashed."""

import shutil
from pathlib import Path

import pytest

import parserx.skills
from parserx.skills import SKILLS, load_skill


@pytest.mark.parametrize("name", SKILLS)
def test_skill_is_a_draft_with_goal_method_output_and_stop(name):
    skill = load_skill(name)
    assert skill.text.startswith("> 草稿，阶段二修订")
    for section in ("## 目标", "## 取证方法", "## 输出要求", "## 停止条件"):
        assert section in skill.text
    assert skill.sha256 == load_skill(name).sha256 and len(skill.sha256) == 64


def test_hash_follows_content(tmp_path):
    for name in SKILLS:
        shutil.copy(Path(parserx.skills.__file__).parent / f"{name}.md", tmp_path / f"{name}.md")
    original = load_skill("figure", tmp_path).sha256
    (tmp_path / "figure.md").write_text(load_skill("figure").text + "\n补充一句。", encoding="utf-8")
    assert load_skill("figure", tmp_path).sha256 != original


def test_unknown_skill():
    with pytest.raises(KeyError):
        load_skill("heading_rules")
