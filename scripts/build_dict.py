"""离线预热公开词典（只查词典，不调用任何 AI）。

用法:
    uv run python scripts/build_dict.py --limit 20            # 前 20 章出现的词
    uv run python scripts/build_dict.py --work-id 2 --limit 50
    uv run python scripts/build_dict.py --top 500             # 全库出现频率最高的 500 个词

词典一次收录永久复用，所以先把高频词灌进去，线上首访就是毫秒级。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "MyDjango.settings")
django.setup()

from django.db import connection  # noqa: E402

from index import dicts  # noqa: E402
from index.models import Contents  # noqa: E402

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def words_from_chapters(work_id=None, limit=0):
    qs = Contents.objects.values("work_id", "path").order_by("work_id", "global_order")
    if work_id:
        qs = qs.filter(work_id=work_id)
    chapters = list(qs.distinct())
    if limit:
        chapters = chapters[:limit]

    counter = Counter()
    for index, item in enumerate(chapters, start=1):
        connection.close()
        segments = Contents.objects.filter(
            work_id=item["work_id"], path=item["path"]
        ).order_by("global_order")
        text = " ".join(s.text for s in segments if s.text)
        counter.update(WORD_RE.findall(text))
        if index % 10 == 0:
            print(f"  扫描 {index}/{len(chapters)} 章，累计 unique 词 {len(counter)}")
    return counter


def words_from_whole_library(top=0):
    """直接按出现次数取高频词（一次聚合，不用逐章扫）。"""
    counter = Counter()
    offset = 0
    batch = 20000
    while True:
        connection.close()
        rows = list(
            Contents.objects.exclude(text__isnull=True)
            .exclude(text="")
            .values_list("text", flat=True)[offset:offset + batch]
        )
        if not rows:
            break
        for text in rows:
            counter.update(WORD_RE.findall(text))
        offset += batch
        print(f"  已处理 {offset} 行，unique 词 {len(counter)}")
    return Counter(dict(counter.most_common(top))) if top else counter


def main() -> int:
    parser = argparse.ArgumentParser(description="预热公开词典")
    parser.add_argument("--work-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=0, help="扫描章节数")
    parser.add_argument("--top", type=int, default=0, help="取全库最高频的 N 个词")
    parser.add_argument("--max-words", type=int, default=0, help="最多收录多少词")
    args = parser.parse_args()

    if args.top:
        counter = words_from_whole_library(args.top)
    else:
        counter = words_from_chapters(args.work_id, args.limit)

    words = [w for w, _ in counter.most_common()]
    if args.max_words:
        words = words[:args.max_words]

    print(f"待收录词: {len(words)}")
    if not words:
        return 0

    step = 200
    for start in range(0, len(words), step):
        chunk = words[start:start + step]
        connection.close()
        dicts.lookup(chunk)
        print(f"  已处理 {min(start + step, len(words))}/{len(words)}")

    from index.models import DictEntry

    known = DictEntry.objects.exclude(source="miss").count()
    print(f"完成。词典现有条目: {known}（其中 miss 已排除）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
