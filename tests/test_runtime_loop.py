"""Our own agent loop (Q86): the model calls the four tools as functions, in-process — here a scripted model."""

import json
from types import SimpleNamespace

from parserx.runtimes.loop import LoopAgent
from tests.test_runtime_hybrid import _context_class, _parse, _summary
from tests.test_runtime_pipeline import pdf  # noqa: F401  (fixture: a native page and a scanned page)
from tests.test_tools_contract import _config


class _Item(SimpleNamespace):
    def model_dump(self, exclude_none=False):
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


def _call(name, arguments, n):
    return _Item(type="function_call", name=name, arguments=json.dumps(arguments, ensure_ascii=False),
                 call_id=f"call-{n}", id=f"fc-{n}")


class ScriptedModel:
    """Reads the page the draft lacks, adopts the reading, sets the new titles' level, submits, reports."""

    def __init__(self):
        self.requests = []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **request):
        self.requests.append(request)
        results = [json.loads(i["output"]) for i in request["input"] if i.get("type") == "function_call_output"]
        n = len(self.requests)
        if not results:
            output = [_call("view_source", {"looks": [{"page": 2, "as": "text"}]}, n)]
        elif len(results) == 1:
            evidence = results[0]["result"]["results"][0]["evidence"]
            output = [_call("edit_draft", {"ops": [{"op": "adopt", "page": 2, "evidence": evidence, "reason": "扫描页"}]},
                            n)]
        elif len(results) == 2:
            titles = [i["target"] for i in results[1]["result"]["issues_opened"] if i["kind"] == "structure_pending"]
            output = [_call("edit_draft", {"ops": [{"op": "set_role", "block": b, "role": "H2", "reason": "节标题"}
                                                   for b in titles]}, n),
                      _call("submit_draft", {}, n + 100)]  # two calls in one turn, in order
        else:
            output = [_Item(type="message", role="assistant", content=[])]
        usage = SimpleNamespace(input_tokens=1000, output_tokens=50,
                                input_tokens_details=SimpleNamespace(cached_tokens=600),
                                output_tokens_details=SimpleNamespace(reasoning_tokens=20),
                                model_dump=lambda: {})
        return SimpleNamespace(output=output, usage=usage, output_text="交稿被接受；没有未解决项。")


def test_the_loop_works_through_the_tools_as_functions(pdf, tmp_path):
    model = ScriptedModel()
    agent = LoopAgent("model-x", "medium", config=_config(), context_class=_context_class(),
                      client_factory=lambda config, timeout: model)
    outcome = _parse(pdf, tmp_path, agent)
    assert outcome.runtime == "hybrid:agent" and outcome.status == "complete", outcome.runtime_detail
    first = model.requests[0]
    assert [t["name"] for t in first["tools"]] == ["read_draft", "view_source", "edit_draft", "submit_draft"]
    assert first["tools"][2]["parameters"]["properties"]["ops"]  # the request model is the function's parameters
    task = first["input"][0]["content"]
    assert "函数调用" in task and "./px" not in task and "**`set_role`**" in task and "{{" not in task
    assert first["store"] is False and len(model.requests) == 4
    record = _summary(tmp_path)["processing"]["agent"]
    assert record["tool_calls"] == 4 and record["model"] == "model-x"
