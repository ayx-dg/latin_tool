"""检查标注模型的真实可用性（线上 API，不依赖缓存）。

用法:
    uv run python scripts/check_gloss.py                      # 用内置样例拉丁句
    uv run python scripts/check_gloss.py --text "Gallia est omnis divisa in partes tres"
    uv run python scripts/check_gloss.py --work-id 1 --path urn:cts:latinLit:phi0978.phi001.perseus-lat2.1.praef
    uv run python scripts/check_gloss.py --repeat 5           # 连打 5 次，观察限流/速率

退出码: 0=对齐且成功，1=失败（配额/鉴权/超时/数量不符）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import django

# 从 scripts/ 直接运行时，项目根目录不在 sys.path 里
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "MyDjango.settings")
django.setup()

from index.views import _build_gloss_prompt, _extract_words, _model  # noqa: E402

SAMPLE = "Gallia est omnis divisa in partes tres."


def classify_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "429" in text or "resource_exhaust" in low or "quota" in low:
        return "QUOTA_OR_RATE_LIMIT(429)"
    if "403" in text or "api key" in low or "permission" in low:
        return "AUTH(403)"
    if "404" in text or "not found" in low:
        return "MODEL_NOT_FOUND(404)"
    if "timeout" in low or "deadline" in low:
        return "TIMEOUT"
    return "OTHER"


def call_provider(text: str):
    """直接打模型，不查缓存。返回 (items, latency, error)。"""
    prompt = _build_gloss_prompt(text)
    start = time.monotonic()
    try:
        response = _model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        items = json.loads(response.text)
    except Exception as exc:  # noqa: BLE001 - 探针需要看到所有失败形态
        return None, time.monotonic() - start, exc
    return items, time.monotonic() - start, None


def fetch_text_from_db(work_id: int, path: str) -> str:
    from index.models import Contents

    segments = Contents.objects.filter(work_id=work_id, path=path).order_by("global_order")
    return " ".join(s.text for s in segments)


def main() -> int:
    parser = argparse.ArgumentParser(description="标注模型可用性探针")
    parser.add_argument("--text", default=None, help="自定义拉丁文本")
    parser.add_argument("--work-id", type=int, default=None)
    parser.add_argument("--path", default=None)
    parser.add_argument("--repeat", type=int, default=1, help="连续调用次数，用于观察限流")
    parser.add_argument("--max-words", type=int, default=0, help=">0 时截断词数，模拟短请求")
    args = parser.parse_args()

    if args.text:
        text = args.text
    elif args.work_id and args.path:
        text = fetch_text_from_db(args.work_id, args.path)
    else:
        text = SAMPLE

    words = _extract_words(text)
    if args.max_words:
        words = words[: args.max_words]
        text = " ".join(words)
    print(f"模型: {_model.model_name}")
    print(f"词数: {len(words)}  样例: {words[:8]}")

    failures = 0
    for i in range(1, args.repeat + 1):
        items, latency, error = call_provider(text)
        if error is not None:
            failures += 1
            print(f"[{i}/{args.repeat}] 失败 {classify_error(error)}  {latency:.1f}s  {str(error)[:160]}")
            continue
        aligned = len(items) == len(words)
        if not aligned:
            failures += 1
        print(
            f"[{i}/{args.repeat}] {'对齐 ✓' if aligned else '错位 ✗'} "
            f"返回 {len(items)}/{len(words)} 条  {latency:.1f}s"
        )
        if items:
            print("      样例:", json.dumps(items[:2], ensure_ascii=False)[:160])

    print(f"结果: {args.repeat - failures}/{args.repeat} 次成功")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
