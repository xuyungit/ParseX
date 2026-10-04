"""Cache retention (Q152): an entry's last use is its file time, entries unused for keep_days go, the layout model
stays; ``parserx parse`` prunes at most once a day; a read-only replay changes nothing."""

import os
import time

from parserx.cache.store import ResponseCache, clear, maybe_prune, prune, usage
from parserx.config.schema import CacheConfig

DAY = 86400.0


def _aged(path, days, now):
    os.utime(path, (now - days * DAY, now - days * DAY))


def test_a_hit_is_a_use_where_the_cache_may_write(tmp_path):
    now = time.time()
    ResponseCache(tmp_path).put("vlm", "ab" * 32, {"text": "x"}, {"model": "m"})
    path = ResponseCache(tmp_path).path("vlm", "ab" * 32)
    _aged(path, 200, now)
    assert ResponseCache(tmp_path, "read_only").get("vlm", "ab" * 32)[0]
    assert path.stat().st_mtime < now - 199 * DAY  # a frozen run's replay changes nothing
    assert ResponseCache(tmp_path).get("vlm", "ab" * 32)[0]
    assert path.stat().st_mtime > now - 1  # used now: kept by pruning


def test_entries_not_used_for_keep_days_go_and_the_model_stays(tmp_path):
    now = time.time()
    cache = ResponseCache(tmp_path)
    cache.put("vlm", "aa" * 32, {"text": "old"}, {})
    cache.put("vlm", "bb" * 32, {"text": "recent"}, {})
    cache.put_derived("reading", "cc" * 32, [])
    _aged(cache.path("vlm", "aa" * 32), 120, now)
    _aged(cache.derived_path("reading", "cc" * 32), 91, now)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "layout.onnx").write_bytes(b"model")
    _aged(tmp_path / "models" / "layout.onnx", 400, now)
    files, size = prune(tmp_path, 90, now=now)
    assert files == 2 and size > 0
    assert not cache.path("vlm", "aa" * 32).exists() and cache.path("vlm", "bb" * 32).exists()
    assert not (tmp_path / "derived" / "reading").exists()  # emptied directories go too
    assert (tmp_path / "models" / "layout.onnx").exists()
    assert usage(tmp_path)["raw"][0] == 1


def test_parse_prunes_at_most_once_a_day_and_only_a_cache_that_keeps_days(tmp_path):
    now = time.time()
    cache = ResponseCache(tmp_path)
    cache.put("vlm", "aa" * 32, {}, {})
    _aged(cache.path("vlm", "aa" * 32), 100, now)
    assert maybe_prune(CacheConfig(mode="read_write", dir=str(tmp_path), keep_days=0), now=now) is None
    assert maybe_prune(CacheConfig(mode="read_only", dir=str(tmp_path), keep_days=90), now=now) is None
    config = CacheConfig(mode="read_write", dir=str(tmp_path), keep_days=90)
    assert maybe_prune(config, now=now)[0] == 1
    cache.put("vlm", "bb" * 32, {}, {})
    _aged(cache.path("vlm", "bb" * 32), 100, now)
    assert maybe_prune(config, now=now + 3600) is None  # pruned within the day: not again
    assert maybe_prune(config, now=now + DAY + 1)[0] == 1


def test_clear_deletes_every_entry_but_the_model(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("ocr", "aa" * 32, {}, {})
    cache.put_derived("layout", "bb" * 32, [])
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "layout.onnx").write_bytes(b"model")
    assert clear(tmp_path)[0] == 2
    assert usage(tmp_path)["raw"][0] == usage(tmp_path)["derived"][0] == 0 and usage(tmp_path)["models"][0] == 1


def test_the_built_in_cache_keeps_ninety_days_and_the_evaluation_config_everything(tmp_path, monkeypatch):
    from pathlib import Path

    from parserx.config.schema import load_config

    monkeypatch.chdir(tmp_path)
    assert load_config().cache.keep_days == 90
    assert load_config(Path(__file__).parents[1] / "configs" / "regression.yaml").cache.keep_days == 0
