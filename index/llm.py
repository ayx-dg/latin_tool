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
from typing import List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# gemini-2.x 已停用；3.5-flash-lite 免费额度更宽松且快 3-4 倍，
# 想要更精准的语法分析可改 LLM_MODEL=gemini-3.5-flash（慢一些）。
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
# 腾讯云 Token Plan 个人版（OpenAI 兼容）：base = https://api.lkeap.cloud.tencent.com/plan/v3
DEFAULT_HY_BASE_URL = "https://api.lkeap.cloud.tencent.com/plan/v3"
DEFAULT_HY_MODEL = "deepseek-v4-flash-202605"


class ProviderError(Exception):
    """模型调用失败。"""


class BaseProvider:
    name = "base"

    def complete(self, prompt: str, json_mode: bool = True) -> str:
        """返回模型输出。json_mode=False 时不要强制 JSON（讲解用纯文本）。"""
        raise NotImplementedError


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self):
        import google.generativeai as genai

        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key:
            raise ProviderError("未配置 GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        # LLM_MODEL 是给 OpenAI 兼容端点用的，Gemini 必须用 Gemini 自己的模型名
        model_name = (
            getattr(settings, "LLM_GEMINI_MODEL", "")
            or DEFAULT_GEMINI_MODEL
        )
        self._model = genai.GenerativeModel(model_name)

    def complete(self, prompt: str, json_mode: bool = True) -> str:
        config = {"response_mime_type": "application/json"} if json_mode else None
        response = self._model.generate_content(
            prompt,
            generation_config=config,
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

    def complete(self, prompt: str, json_mode: bool = True) -> str:
        system = ("You are a Latin philology assistant. Always answer with valid JSON only."
                  if json_mode else
                  "You are a Latin philology assistant teaching a Chinese-speaking student.")
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        response = self.client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            **extra,
        )
        return response.choices[0].message.content or ""


_PROVIDERS = {
    GeminiProvider.name: GeminiProvider,
    OpenAICompatibleProvider.name: OpenAICompatibleProvider,
}

_provider_instance: Optional[BaseProvider] = None
_last_provider: Optional[str] = None


def provider_chain() -> List[str]:
    """按优先级返回 provider 名称。前面的失败会自动回退到下一个。

    默认：腾讯云 Token Plan（HY_API_KEY）优先，其次 Gemini（GEMINI_API_KEY）；
    可用 LLM_PROVIDERS=openai_compatible,gemini 显式指定顺序。
    """
    configured = getattr(settings, "LLM_PROVIDERS", "")
    if configured:
        return [name.strip() for name in configured.split(",") if name.strip()]

    chain = []
    if getattr(settings, "LLM_API_KEY", ""):
        chain.append(OpenAICompatibleProvider.name)
    if getattr(settings, "GEMINI_API_KEY", ""):
        chain.append(GeminiProvider.name)
    return chain or [getattr(settings, "LLM_PROVIDER", GeminiProvider.name)]


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


def complete(prompt: str, json_mode: bool = True) -> str:
    """依次尝试 provider_chain()，全部失败才抛 ProviderError。"""
    global _last_provider
    errors = []
    for name in provider_chain():
        if name not in _PROVIDERS:
            errors.append(f"{name}: 未知 provider")
            continue
        try:
            result = _PROVIDERS[name]().complete(prompt, json_mode=json_mode)
            _last_provider = name
            logger.info("provider %s 调用成功", name)
            return result
        except Exception as exc:  # noqa: BLE001 - 换下一个 provider
            logger.warning("provider %s 调用失败，尝试下一个: %s", name, str(exc)[:200])
            errors.append(f"{name}: {exc}")
    raise ProviderError("; ".join(errors) or "没有可用 provider")


def get_last_provider() -> str:
    """返回最近一次 complete() 成功的 provider 名称。"""
    if _last_provider:
        return _last_provider
    # 多 worker 下 _last_provider 可能不可靠，回退到 chain 首选
    chain = provider_chain()
    return chain[0] if chain else getattr(settings, "LLM_PROVIDER", "")


def reset_provider() -> None:
    """切换环境变量后需要重置（测试用）。"""
    global _provider_instance, _last_provider
    _provider_instance = None
    _last_provider = None
