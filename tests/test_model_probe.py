"""The model probe's scoring and request loop (scripts/model_probe.py), offline: made-up questions, a fake service."""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import model_probe as mp  # noqa: E402


def _place(at, want=None, wrong="", abilities=("A3",), mode="placed"):
    return {"at": at, "want": want or at, "wrong": wrong, "abilities": list(abilities), "mode": mode, "note": ""}


def _trap(printed, corrected, at=None):
    return {"printed": printed, "corrected": corrected, "at": at or printed, "abilities": ["A6"], "note": ""}


TEXT = "设 $x_{1}$ 为长度，单位 m，取 No 个样本，见文献$^{[3]}$。"


def test_a_place_is_held_where_the_reading_writes_it_with_its_script():
    place = _place("$x_{1}$ 为", "_{1}", "1")
    assert mp.place_held(place, TEXT, TEXT, False)
    assert mp.place_held(place, TEXT, "设 x₁ 为长度，单位 m，取 No 个样本，见文献[3]。", False)  # Unicode subscript
    assert not mp.place_held(place, TEXT, "设 x1 为长度，单位 m，取 No 个样本，见文献[3]。", False)
    citation = _place("文献$^{[3]}$", "^{[3]}", "[3]")
    assert mp.place_held(citation, TEXT, "设 $x_1$ 为长度，单位 m，取 No 个样本，见文献<sup>[3]</sup>。", False)
    assert not mp.place_held(citation, TEXT, "设 $x_1$ 为长度，单位 m，取 No 个样本，见文献[3]。", False)


def test_a_place_needs_its_stretch_read_not_just_the_letters_somewhere():
    place = _place("$x_{1}$ 为", "_{1}", "1")
    assert not mp.place_held(place, TEXT, "取 No 个样本。", False)


def test_a_wrong_reading_at_the_place_fails_even_when_the_right_letter_is_near():
    text = "阻尼因子 $\\mu_{k}$ 初值"
    place = _place("$\\mu_{k}$ 初值", "\\mu_{k}", "H_{k}", ["A5"])
    assert mp.place_held(place, text, "阻尼因子 μ_k 初值".replace("μ_k", "$\\mu_k$"), False)
    assert not mp.place_held(place, text, "阻尼因子 $H_k$ 初值", False)


def test_text_places_compare_symbols_and_words_in_one_notation():
    place = _place("≥20支/天", abilities=["A2"], mode="text")
    assert mp.place_held(place, "", "重度吸烟者 $\\geq$ 20 支/天", False)
    assert not mp.place_held(place, "", "重度吸烟者 >20支/天", False)
    assert mp.place_held(_place("0.1～0.5", mode="text"), "", "取 0.1~0.5", False)


def test_a_trap_is_printed_corrected_or_something_else():
    trap = _trap("No", ["$N_0$", "N₀"], at="取 No 个")
    assert mp.trap_outcome(trap, TEXT, TEXT, False) == "printed"
    assert mp.trap_outcome(trap, TEXT, TEXT.replace("No", "$N_0$"), False) == "corrected"
    assert mp.trap_outcome(trap, TEXT, TEXT.replace("No", "N₀"), False) == "corrected"
    assert mp.trap_outcome(trap, TEXT, TEXT.replace("No", "NO"), False) == "other"


def test_a_trap_whose_correction_adds_a_character():
    text = "the components [(11), (22)] of the tensor"
    trap = _trap("[(11), (22)]", ["[($\\sigma_{11}$), ($\\sigma_{22}$)]", "[(σ11), (σ22)]"], at="components [(11), (22)]")
    assert mp.trap_outcome(trap, text, text, False) == "printed"
    assert mp.trap_outcome(trap, text, text.replace("[(11), (22)]", "[(σ11), (σ22)]"), False) == "corrected"
    assert mp.trap_outcome(trap, text, text.replace("[(11), (22)]", "[($\\sigma_{11}$), ($\\sigma_{22}$)]"),
                           False) == "corrected"


def test_f1_takes_out_only_the_optional_stretches_the_answer_does_not_have():
    answer = "the fifth business day after the date"
    assert mp.seq_f1(answer, answer, optional=["business day."]) == 1.0
    assert mp.seq_f1("business day.\n" + answer, answer, optional=["business day."]) == 1.0
    assert mp.seq_f1("business day.\n" + answer, answer) < 1.0
    assert mp.seq_f1("A1 B2", "A1 B2", keep=str.isdigit) == 1.0
    assert mp.seq_f1("x₁", "$x_{1}$", scripts=True) == 1.0 and mp.seq_f1("x1", "$x_{1}$", scripts=True) < 1.0


def test_instruction_problems():
    case = {"answer": "正文"}
    assert mp.instruction_issues(case, "transcribe", "") == ["空回答"]
    assert "有前言" in mp.instruction_issues(case, "transcribe", "以下是抄写结果：\n正文")
    assert "多余的代码围栏" in mp.instruction_issues(case, "transcribe", "```\n正文\n```")
    assert "附加说明" in mp.instruction_issues(case, "transcribe", "正文\n注：原件模糊")
    assert mp.instruction_issues({"answer": "```python\nx\n```"}, "transcribe", "```python\nx\n```") == []
    assert mp.instruction_issues(case, "ask", "图标") == []


def test_a_question_is_right_when_an_accepted_answer_comes_before_a_rejected_one():
    case = {"accept": ["-4.80", "-4.8"], "reject": ["-4.66"]}
    assert mp.score_question(case, " −4.80。").components["question"] == 1.0
    assert mp.score_question(case, "实测值是 -4.80 mm，不是 -4.66").components["question"] == 1.0
    wrong = mp.score_question(case, "是 -4.66（不是 -4.80）")
    assert wrong.components["question"] == 0.0 and wrong.review
    letter = {"accept": ["字母l"], "reject": ["数字1"], "exact": ["l"]}
    assert mp.score_question(letter, "l").components["question"] == 1.0
    assert mp.score_question(letter, "是字母 l，不是数字 1").components["question"] == 1.0
    assert mp.score_question(letter, "是数字 1").components["question"] == 0.0


FIGURE = {"figure_type": "chart", "points": [{"text": "折线图", "groups": [["折线", "曲线"]]},
                                            {"text": "两条线", "groups": [["甲"], ["乙"]]}],
          "allowed_numbers": ["10", "20"], "absent": ["丙"]}


def test_a_figure_counts_points_and_numbers_not_on_it():
    good = mp.score_figure(FIGURE, json.dumps({"type": "chart", "caption": "甲、乙两条折线，乙在约 15 处最高，终值 20"}))
    assert good.components["figure"] == 1.0 and good.components["invented"] == []
    bad = mp.score_figure(FIGURE, json.dumps({"type": "diagram", "caption": "甲与丙的曲线，最大 35"}))
    assert bad.components["figure_recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert bad.components["invented"] == ["35", "丙"] and bad.components["figure"] == pytest.approx(1 / 6, abs=1e-4)
    assert mp.score_figure(FIGURE, "not json").issues == ["不合格式"]


def test_a_table_answer_is_scored_by_cells_and_a_bad_one_fails_the_format():
    case = {"kind": "table", "group": "table", "abilities": ["A7"], "math": False,
            "answer": "| a | b |\n|---|---|\n| 1 | 2 |"}
    html = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
    good = mp.score_table(case, json.dumps({"table_html": html, "undetermined": []}))
    assert good.components["table"] == 1.0
    bad = mp.score_table(case, "{}")
    assert bad.components["table"] == 0.0 and bad.issues == ["不合格式"]


def test_formulas_are_compared_as_display_formulas_and_checked_for_rendering():
    case = {"kind": "formula", "group": "formula", "abilities": ["A4"], "math": True, "matrix": True,
            "answer": "$$A=\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix} \\tag{1}$$"}
    same = mp.score_text(case, "A=\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}")  # no $$: read as one formula
    assert same.components["formula"] == 1.0 and same.components["matrix"] == 1.0
    broken = mp.score_text(case, "$$A=\\begin{pmatrix} a & b \\\\ d & c \\end{pmatrix}")
    assert broken.components["renders"] == 0.0 and broken.components["matrix"] == 0.5


def _cases(tmp_path: Path) -> dict:
    img = tmp_path / "images" / "a.png"
    img.parent.mkdir()
    Image.new("RGB", (20, 10), "white").save(img)
    cases = [
        {"id": "X1", "group": "copy", "kind": "transcribe", "image": "images/a.png", "abilities": ["A1", "A3"],
         "answer": TEXT, "required": [_place("$x_{1}$ 为", "_{1}", "1")], "traps": [_trap("No", ["$N_0$"], "取 No 个")]},
        {"id": "X2", "group": "question", "kind": "ask", "image": "images/a.png", "abilities": ["A2"],
         "answer": "400", "question": "值是多少？", "accept": ["400"]},
    ]
    (tmp_path / "cases.json").write_text(json.dumps({"version": "t", "cases": cases, "stability": ["X2"]},
                                                     ensure_ascii=False))
    return mp.load_cases(tmp_path)


class FakeService:
    calls = 0
    fail_first = False

    def __init__(self):
        self.usage_hook = None

    def describe_image(self, image, prompt, *, context="", **kwargs):
        FakeService.calls += 1
        if FakeService.fail_first and FakeService.calls == 1:
            raise ConnectionError("reset")
        self.usage_hook("m", 100, 0, 10)
        return "400" if context.startswith("问题") else TEXT.replace("No", "$N_0$")


def test_requests_are_asked_once_cached_and_rescored(tmp_path):
    data = _cases(tmp_path)
    reqs = mp.build_requests(data, repeat=3, editor=False, only=None)
    assert [(r.case, r.repeat) for r in reqs] == [("X1", 0), ("X2", 0), ("X2", 1), ("X2", 2)]
    FakeService.calls, FakeService.fail_first = 0, True
    cache = tmp_path / "cache"
    kw = dict(model="m", name=None, effort="low", budget=1000, workers=2, cache=cache, log=lambda _: None)
    answers = mp.run_requests(reqs, make_service=FakeService, rescore=False, **kw)
    assert FakeService.calls == 5  # four keys, one transient failure asked again
    assert len(list((cache / "m" / "low").glob("*.json"))) == 4 and not list(cache.rglob("*.tmp"))
    again = mp.run_requests(reqs, make_service=None, rescore=True, **kw)
    assert [a["text"] for a in again] == [a["text"] for a in answers] and FakeService.calls == 5
    only = mp.build_requests(data, repeat=3, editor=False, only={"A3"})
    assert [r.case for r in only] == ["X1"]
    summary = mp.summarize(data, answers, meta={"model": "m", "effort": "low", "budget": 1000, "date": "",
                                                "wall_seconds": 0, "asked": 4})
    x1 = summary["cases_detail"]["X1"]
    assert x1["places"][0]["held"] and x1["traps"] == [{"printed": "No", "outcome": "corrected"}]
    assert summary["abilities"]["A6"]["score"] == 0.0 and summary["abilities"]["A2"]["score"] == 1.0
    assert summary["abilities"]["A11"]["score"] == 1.0 and summary["traps"] == {"corrected": 1}


def test_a_failed_or_truncated_answer_is_counted_apart_not_as_wrong(tmp_path):
    data = _cases(tmp_path)
    answers = [{"case": "X1", "variant": "main", "repeat": 0, "kind": "transcribe", "text": "", "error": "cut",
                "error_kind": "truncated", "usage": [], "seconds": 1},
               {"case": "X2", "variant": "main", "repeat": 0, "kind": "ask", "text": "400", "error_kind": None,
                "usage": [], "seconds": 1}]
    summary = mp.summarize(data, answers, meta={"model": "m", "effort": "low", "budget": 1, "date": "",
                                                "wall_seconds": 0, "asked": 0})
    assert summary["failures"] == [{"case": "X1", "kind": "truncated", "error": "cut"}]
    assert summary["abilities"]["A1"]["n"] == 0 and summary["total"] == 1.0


def test_a_structured_answer_that_is_not_json_is_asked_once_more(tmp_path):
    answers = iter(["not json", json.dumps({"type": "chart", "caption": "甲、乙两条折线"})])

    class Service:
        usage_hook = None

        def describe_image(self, *args, **kwargs):
            self.usage_hook("m", 10, 0, 5)
            return next(answers)

    img = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(img)
    req = mp.Request("P", "main", 0, "figure", "p", "", img, "json_schema", {}, "s")
    record = mp.ask_one(Service, req, 100)
    assert record["unparsed"] == "not json" and json.loads(record["text"])["type"] == "chart"
    case = {**FIGURE, "kind": "figure", "abilities": ["A8"]}
    assert "首答不合格式" in mp.score_answer(case, {**record, "variant": "main"}).issues


def test_a_place_inside_mathematics_is_found_and_tested_whole():
    text = "当 $ \\eta_{k}>0.75 $"
    place = _place("\\eta_{k}", wrong="n_{k}", abilities=["A5"])
    assert mp._window(text, "\\eta_{k}", False) == (1, 3)
    assert mp.place_held(place, text, "当 $\\boldsymbol{\\eta}_k > 0.75$，", False)
    assert not mp.place_held(place, text, "当 $\\eta_{r} > 0.75$", False)  # the subscript is part of the place
    assert not mp.place_held(place, text, "当 $n_k > 0.75$", False)


def test_overrides_change_the_cache_key_and_survive_the_entry(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(img)
    req = mp.Request("X", "main", 0, "transcribe", "p", "", img, "off", None, "s")
    plain = mp.request_key("m", None, "low", req, 10)
    hires = mp.request_key("m", None, "low", req, 10, ["services.vlm.extra_body={vl_high_resolution_images: true}"])
    assert plain != hires and plain == mp.request_key("m", None, "low", req, 10, [])
    _make, _prices, served = mp.service_factory("gpt-6-sol", "low", "gpt-6.1-sol")
    assert served == "gpt-6.1-sol"  # the name is applied after the entry, not written over by it


def test_numbers_after_chinese_are_checked_and_an_estimate_may_be_negative():
    fig = {**FIGURE, "allowed_numbers": ["10", "20"]}
    s = mp.score_figure(fig, json.dumps({"type": "chart", "caption": "甲、乙两条折线，总宽35，最低约-0.48，终值20"}))
    assert s.components["invented"] == ["35"]
