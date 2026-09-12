"""真实模型连通性测试（默认跳过，避免日常跑测试消耗额度）。

运行:
    RUN_LIVE_GLOSS=1 uv run pytest index/test_gloss_live.py -v -s

用途：换 key、换模型、怀疑被限流时用，验证「能连通 + 返回数量对齐」。
"""

import json
import os

import pytest

from index.views import _align_items, _build_gloss_prompt, _extract_words, _model

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_GLOSS") != "1",
    reason="设置 RUN_LIVE_GLOSS=1 才会真实调用模型",
)

SAMPLE = "Gallia est omnis divisa in partes tres."


def _call():
    return _model.generate_content(
        _build_gloss_prompt(SAMPLE),
        generation_config={"response_mime_type": "application/json"},
        request_options={"timeout": 60},
    )


def test_provider_is_reachable():
    response = _call()
    assert response.text, "模型返回为空"


def test_provider_returns_aligned_items():
    words = _extract_words(SAMPLE)
    items, missing = _align_items(words, json.loads(_call().text))
    assert missing == 0, f"{missing}/{len(words)} 个词没被标注"
    assert len(items) == len(words)
    assert all(i["m"] for i in items), "存在没有释义的词"
