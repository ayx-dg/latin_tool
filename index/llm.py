"""标注模型 provider 抽象层。

后端由环境变量决定，代码不动即可切换：

    LLM_PROVIDER=gemini              # Google AI Studio（默认，需 GEMINI_API_KEY）
    LLM_PROVIDER=openai_compatible   # 任何 OpenAI 兼容端点
    LLM_BASE_URL=https://api.deepseek.com
    LLM_MODEL=deepseek-chat
    LLM_API_KEY=...

openai_compatible 覆盖：DeepSeek、OpenRouter、Groq、Azure AI Foundry、
以及 Gemini 自己的 OpenAI 兼容端点 https://generativelanguage.googleapis.com/v1beta/openai
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# gemini-2.x 已停用；3.5-flash-lite 免费额度更宽松且快 3-4 倍，
# 想要更精准的语法分析可改 LLM_MODEL=gemini-3.5-flash（慢一些）。
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class ProviderError(Exception):
    """模型调用失败。"""


class BaseProvider:
    name = "base"

    def complete(self, prompt: str) -> str:
        """返回模型输出的原始文本（JSON 字符串）。"""
        raise NotImplementedError


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self):
        import google.generativeai as genai

        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key:
            raise ProviderError("未配置 GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        model_name = getattr(settings, "LLM_MODEL", "") or DEFAULT_GEMINI_MODEL
        self._model = genai.GenerativeModel(model_name)

    def complete(self, prompt: str) -> str:
        response = self._model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
            request_options={"timeout": float(getattr(settings, "GLOSS_TIMEOUT", 60))},
        )
        return response.text


class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"

    def __init__(self):
        from openai import OpenAI

        base_url = getattr(settings, "LLM_BASE_URL", "") or None
        api_key = getattr(settings, "LLM_API_KEY", "")
        if not api_key:
            raise ProviderError("未配置 LLM_API_KEY")
        if not getattr(settings, "LLM_MODEL", ""):
            raise ProviderError("未配置 LLM_MODEL")
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(getattr(settings, "GLOSS_TIMEOUT", 60)),
        )

    def complete(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a Latin philology assistant. Always answer with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content or ""


_PROVIDERS = {
    GeminiProvider.name: GeminiProvider,
    OpenAICompatibleProvider.name: OpenAICompatibleProvider,
}

_provider_instance: Optional[BaseProvider] = None


def get_provider() -> BaseProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    name = (getattr(settings, "LLM_PROVIDER", "") or "").strip()
    if name not in _PROVIDERS:
        raise ProviderError(f"未知的 LLM_PROVIDER: {name!r}，可选: {', '.join(_PROVIDERS)}")

    _provider_instance = _PROVIDERS[name]()
    logger.info("使用标注 provider: %s", name)
    return _provider_instance


def complete(prompt: str) -> str:
    """调用当前 provider。失败抛 ProviderError。"""
    try:
        return get_provider().complete(prompt)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(str(exc)) from exc


def reset_provider() -> None:
    """切换环境变量后需要重置（测试用）。"""
    global _provider_instance
    _provider_instance = None
