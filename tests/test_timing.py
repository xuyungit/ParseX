"""Where the time goes (speed plan §2): steps timed with their requests, model usage and local model work."""

from parserx.runtimes.actions import AgentTally
from parserx.scheduling.meter import LOCAL, RequestMeter
from parserx.scheduling.timing import StepClock


def test_a_step_records_what_happened_in_it_and_only_that():
    meter = RequestMeter()
    meter.request("vlm")
    clock = StepClock(meter)
    clock.start("describe")
    meter.request("vlm")
    meter.usage("vlm", input_tokens=100, cached_input_tokens=0, output_tokens=10, usd=0.5, model="qwen")
    with LOCAL.time("reading"):
        pass
    clock.start("transcribe")  # ends "describe"
    meter.request("ocr", pages=3)
    clock.stop()
    describe, transcribe = clock.steps
    assert (describe.step, describe.requests, describe.usd) == ("describe", {"vlm": 1}, 0.5)
    assert describe.models["qwen"]["calls"] == 1 and describe.local["reading"]["calls"] == 1
    assert (transcribe.requests, transcribe.pages, transcribe.models, transcribe.local) == ({"ocr": 1}, {"ocr": 3}, {}, {})


def test_the_meter_keeps_usage_per_model():
    meter = RequestMeter()
    for model in ("a", "a", "b"):
        meter.usage("vlm", input_tokens=10, cached_input_tokens=0, output_tokens=1, usd=0.25, model=model)
    models = meter.snapshot().models
    assert models["a"]["calls"] == 2 and models["a"]["usd"] == 0.5 and models["b"]["input"] == 10


def test_the_agent_tally_times_each_tool_from_its_envelope():
    tally = AgentTally()
    for tool, s in (("read_draft", 0.5), ("view_source", 2.0), ("view_source", 1.0)):
        tally.add({"type": "call", "tool": tool, "envelope": {"ok": True, "cost": {"wall_s": s}}})
    assert tally.tools == {"read_draft": {"calls": 1, "s": 0.5}, "view_source": {"calls": 2, "s": 3.0}}


def test_a_scan_engine_step_spreads_its_pages_over_the_requests_it_may_send_at_once():
    from parserx.scheduling import spread

    assert [len(b) for b in spread(list(range(48)), at_most=100, workers=4)] == [12, 12, 12, 12]
    assert [len(b) for b in spread(list(range(250)), at_most=50, workers=4)] == [50] * 5
    assert spread([1, 2], at_most=100, workers=4) == [[1], [2]] and spread([], at_most=9, workers=4) == []
