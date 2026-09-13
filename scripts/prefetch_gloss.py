"""离线批量生成标注，填满 GlossCache。

用途：Gemini 免费额度小（RPD/RPM 都有限），在线等用户点一次算一次必然超时或 429。
提前把热门章节算好写进数据库，线上就只剩查缓存，体验是"秒开"。

用法:
    # 先看会跑哪些、多少章（不调用 API）
    uv run python scripts/prefetch_gloss.py --dry-run --limit 20

    # 真的跑：前 10 章，每章之间睡 4 秒
    uv run python scripts/prefetch_gloss.py --limit 10 --sleep 4

    # 只跑某一部作品
    uv run python scripts/prefetch_gloss.py --work-id 1 --limit 50

    # 从某一部作品的某个 path 之后继续
    uv run python scripts/prefetch_gloss.py --work-id 1 --after "urn:...praef"

遇到配额/限流错误会直接停下并提示，不会硬烧额度。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "MyDjango.settings")
django.setup()

from django.db.models import Min  # noqa: E402

from index.models import Contents, Works  # noqa: E402
from index.views import _missing_count, get_or_create_gloss  # noqa: E402

QUOTA_MARKERS = ("429", "resource_exhaust", "quota", "rate limit", "rate_limit")


def list_chapters(work_id=None, after=None, limit=0):
    qs = Contents.objects.values("work_id", "path").annotate(
        min_order=Min("global_order")
    ).order_by("work_id", "min_order")
    if work_id:
        qs = qs.filter(work_id=work_id)
    chapters = list(qs.values_list("work_id", "path"))
    if after:
        try:
            idx = next(i for i, (_, p) in enumerate(chapters) if p == after)
            chapters = chapters[idx + 1:]
        except StopIteration:
            pass
    return chapters[:limit] if limit else chapters


def build_text(work_id: int, path: str) -> str:
    """必须和 views.work_detail 拼文本的方式完全一致，否则缓存永远命不中。"""
    segments = Contents.objects.filter(work_id=work_id, path=path).order_by("global_order")
    full_text = " ".join(s.text for s in segments if s.text)
    return re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)


def main() -> int:
    parser = argparse.ArgumentParser(description="离线预热标注缓存")
    parser.add_argument("--work-id", type=int, default=None)
    parser.add_argument("--after", default=None, help="从该 path 之后的章节开始")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少章，0=不限")
    parser.add_argument("--sleep", type=float, default=3.0, help="每次调用之间的间隔秒数")
    parser.add_argument("--max-words", type=int, default=1200, help="超过该词数的章节跳过，避免单次请求过大")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chapters = list_chapters(args.work_id, args.after, args.limit)
    print(f"待处理章节: {len(chapters)}  dry_run={args.dry_run}")

    done = skipped = failed = 0
    for i, (work_id, path) in enumerate(chapters, start=1):
        text = build_text(work_id, path)
        words = len(re.findall(r'[^\W\d_]+', text, re.UNICODE))
        title = Works.objects.filter(id=work_id).values_list("title", flat=True).first()
        label = f"[{i}/{len(chapters)}] work={work_id} {title or ''} words={words}"
        if not text.strip() or words == 0:
            print(f"{label} -> 空章节，跳过")
            skipped += 1
            continue
        if words > args.max_words:
            print(f"{label} -> 超过 {args.max_words} 词，跳过")
            skipped += 1
            continue
        if args.dry_run:
            print(f"{label} -> {path[:60]}")
            continue

        try:
            gloss_data, text_hash = get_or_create_gloss(text)
            missing = _missing_count(gloss_data)
            status = "ok" if missing == 0 else f"缺 {missing} 词(未缓存)"
            print(f"{label} -> {status} hash={text_hash[:8]}")
            done += 1
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            print(f"{label} -> 失败: {msg[:200]}")
            failed += 1
            if any(m in msg.lower() for m in QUOTA_MARKERS):
                print("\n遇到配额/限流，停止。稍后重试或调大 --sleep。")
                break
        time.sleep(args.sleep)

    print(f"\n完成: 成功 {done}，跳过 {skipped}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
