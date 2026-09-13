"""（可选）用 AI 给词典条目补中文释义。离线跑，不在请求路径里。

默认不启用——逐词释义来自公开词典，AI 只负责讲解。
想要中文逐词释义时再跑：

    uv run python scripts/fill_dict_zh.py --limit 200        # 补 200 条
    uv run python scripts/fill_dict_zh.py --limit 200 --sleep 2

策略：按词频从高到低，批量（每批 40 个）让模型翻成中文，只填 gloss_zh。
已经填过的会跳过；遇到配额错误直接停下。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "MyDjango.settings")
django.setup()

from django.db import connection  # noqa: E402

from index import llm  # noqa: E402
from index.models import DictEntry  # noqa: E402

BATCH = 40
QUOTA_MARKERS = ("429", "resource_exhaust", "quota", "rate limit", "rate_limit")

PROMPT = """你是拉丁语词典编纂者。把下面的拉丁语词条翻成中文释义。

要求：
- 每个词给一个简短中文释义（1-6 字）和词性/形态说明（中文，可为空）
- 专有名词给通用中文译名
- 只输出 JSON：{"items":[{"w":"原词","m":"中文释义","g":"词性形态"}]}
- 数量和顺序必须与输入一致

词条：
{words}
"""


def fill(limit: int, sleep: float) -> int:
    qs = (
        DictEntry.objects.exclude(source="miss")
        .filter(gloss_zh="")
        .exclude(gloss_en="")[:limit]
    )
    entries = list(qs)
    if not entries:
        print("没有待填充的条目")
        return 0

    done = 0
    for start in range(0, len(entries), BATCH):
        chunk = entries[start:start + BATCH]
        words = [e.word_form for e in chunk]
        try:
            raw = llm.complete(PROMPT.format(words="\n".join(f"{i+1}. {w}" for i, w in enumerate(words))))
            items = json.loads(raw).get("items", [])
            by_word = {it.get("w"): it for it in items if it.get("w")}
        except Exception as exc:  # noqa: BLE001
            print(f"  批次失败: {str(exc)[:160]}")
            if any(m in str(exc).lower() for m in QUOTA_MARKERS):
                print("遇到配额限制，停止。")
                break
            continue

        for entry in chunk:
            item = by_word.get(entry.word_form)
            if item and item.get("m"):
                entry.gloss_zh = item["m"]
                if item.get("g") and not entry.morph:
                    entry.morph = item["g"]
                entry.save(update_fields=["gloss_zh", "morph", "updated_at"])
                done += 1
        connection.close()
        print(f"  已处理 {min(start + BATCH, len(entries))}/{len(entries)}，成功 {done}")
        time.sleep(sleep)

    print(f"完成：补充中文释义 {done} 条")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="给词典补中文释义（可选，离线）")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()
    return fill(args.limit, args.sleep)


if __name__ == "__main__":
    raise SystemExit(main())
