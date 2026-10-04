"""``parserx cache``: the response cache's size and age, and pruning or clearing it by hand (Q152)."""

from __future__ import annotations

import argparse
from datetime import datetime

from parserx.cache.store import KINDS, clear, prune, usage

TEXT = {
    "zh": {"dir": "缓存  {dir}", "kind": {"raw": "服务的回答", "derived": "本地读数与版面", "jobs": "扫描引擎作业",
                                         "models": "版面模型（不是缓存，不清理）"},
           "row": "  {name:<16}{files:>7} 个  {mb:>8.1f} MB{oldest}", "oldest": "  最久未用：{date}",
           "keep": "自动清理：{days} 天未用的条目在 parserx parse 启动时删除（cache.keep_days）",
           "keep_off": "自动清理：关（cache.keep_days: 0）",
           "pruned": "删除了 {files} 个 {days} 天未用的条目（{mb:.1f} MB）", "cleared": "删除了全部 {files} 个条目（{mb:.1f} MB）"},
    "en": {"dir": "cache  {dir}", "kind": {"raw": "service answers", "derived": "local readings, layout",
                                          "jobs": "scan engine jobs", "models": "layout model (not cache, kept)"},
           "row": "  {name:<32}{files:>7}  {mb:>8.1f} MB{oldest}", "oldest": "  oldest last use: {date}",
           "keep": "automatic pruning: entries not used for {days} days go when parserx parse starts (cache.keep_days)",
           "keep_off": "automatic pruning: off (cache.keep_days: 0)",
           "pruned": "{files} entries not used for {days} days deleted ({mb:.1f} MB)",
           "cleared": "all {files} entries deleted ({mb:.1f} MB)"},
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", help="a config file over the built-in, project and personal ones")
    parser.add_argument("--prune", action="store_true", help="delete the entries not used for keep_days days now")
    parser.add_argument("--days", type=int, help="with --prune: this many days instead of cache.keep_days")
    parser.add_argument("--clear", action="store_true", help="delete every entry (the layout model stays)")
    parser.add_argument("--lang", choices=["zh", "en"])


def run(args: argparse.Namespace) -> int:
    from parserx.config.schema import load_config_with_result

    config = load_config_with_result(args.config).config
    text = TEXT[args.lang or config.output.lang]
    root = config.cache.dir
    if args.clear:
        files, size = clear(root)
        print(text["cleared"].format(files=files, mb=size / 1e6))
    elif args.prune:
        days = args.days if args.days is not None else (config.cache.keep_days or 90)
        files, size = prune(root, days)
        print(text["pruned"].format(files=files, days=days, mb=size / 1e6))
    print(text["dir"].format(dir=root))
    for kind, (files, size, oldest) in usage(root).items():
        when = text["oldest"].format(date=datetime.fromtimestamp(oldest).strftime("%Y-%m-%d")) \
            if oldest and kind in KINDS else ""
        print(text["row"].format(name=text["kind"][kind], files=files, mb=size / 1e6, oldest=when))
    print(text["keep"].format(days=config.cache.keep_days) if config.cache.keep_days else text["keep_off"])
    return 0
