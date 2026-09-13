import json

import pytest
from django.test import override_settings
from unittest.mock import MagicMock, patch

import django
from index import views
from index.models import ChapterNote, DictEntry, GlossCache
from index import llm
from index.views import (
    FAILED_MARK,
    _extract_words,
    _build_gloss_prompt,
    _fallback_gloss,
    _parse_items,
    _align_items,
    _call_provider_with_retry,
    _expand_unique,
    get_or_create_gloss,
    check_rate_limit,
)


class TestExtractWords:
    def test_extract_words_returns_only_alphabetic_unicode_words(self):
        text = "Arma virumque canō, Trōiae quī prīmus ab ōrīs!"
        result = _extract_words(text)
        assert result == ["Arma", "virumque", "canō", "Trōiae", "quī", "prīmus", "ab", "ōrīs"]

    def test_extract_words_handles_empty_and_whitespace(self):
        assert _extract_words("") == []
        assert _extract_words("   ") == []


class TestBuildGlossPrompt:
    def test_prompt_contains_indexed_words(self):
        prompt = _build_gloss_prompt("arma virum")
        assert "1. arma" in prompt
        assert "2. virum" in prompt

    def test_prompt_requests_indexed_items(self):
        prompt = _build_gloss_prompt("arma virum")
        assert "items" in prompt
        assert "1. arma" in prompt and "2. virum" in prompt
        # 明确要求返回数量，模型才知道要对齐
        assert "exactly 2 objects" in prompt


class TestFallbackGloss:
    def test_returns_fallback_for_each_word(self):
        result = _fallback_gloss("arma virum")
        assert len(result) == 2
        assert result[0] == {"w": "arma", "m": "解析失败", "g": ""}
        assert result[1] == {"w": "virum", "m": "解析失败", "g": ""}

    def test_fallback_splits_on_whitespace_when_regex_finds_nothing(self):
        result = _fallback_gloss("123 456")
        assert len(result) == 2
        assert result[0] == {"w": "123", "m": "解析失败", "g": ""}


class TestParseItems:
    def test_unwraps_items_key(self):
        assert _parse_items({"items": [{"i": 1, "m": "a"}]}) == [{"i": 1, "m": "a"}]

    def test_accepts_bare_array(self):
        assert _parse_items([{"i": 1}]) == [{"i": 1}]

    def test_returns_empty_for_garbage(self):
        assert _parse_items("not json") == []
        assert _parse_items({"foo": 1}) == []


class TestAlignItems:
    def test_aligns_by_index_and_preserves_order(self):
        words = ["arma", "virum", "cano"]
        raw = {"items": [
            {"i": 1, "m": "武器", "g": "名词"},
            {"i": 2, "m": "男人", "g": "名词"},
            {"i": 3, "m": "歌唱", "g": "动词"},
        ]}
        items, missing = _align_items(words, raw)
        assert missing == 0
        assert [i["m"] for i in items] == ["武器", "男人", "歌唱"]
        assert [i["w"] for i in items] == words

    def test_tolerates_shuffled_indexes(self):
        words = ["a", "b", "c"]
        raw = {"items": [
            {"i": 3, "m": "三"},
            {"i": 1, "m": "一"},
            {"i": 2, "m": "二"},
        ]}
        items, missing = _align_items(words, raw)
        assert missing == 0
        assert [i["m"] for i in items] == ["一", "二", "三"]

    def test_falls_back_to_position_when_index_missing(self):
        words = ["a", "b"]
        items, missing = _align_items(words, [{"m": "一"}, {"m": "二"}])
        assert missing == 0
        assert [i["m"] for i in items] == ["一", "二"]

    def test_counts_missing_when_model_drops_words(self):
        words = ["a", "b", "c"]
        items, missing = _align_items(words, {"items": [{"i": 1, "m": "一"}]})
        assert missing == 2
        assert items[0]["m"] == "一"
        assert items[1]["m"] == ""

    def test_empty_payload_marks_everything_missing(self):
        items, missing = _align_items(["a", "b"], {})
        assert missing == 2
        assert all(i["m"] == "" for i in items)


class TestCallProviderRetry:
    def test_returns_aligned_result_without_retry(self):
        words = ["arma"]
        payload = {"items": [{"i": 1, "m": "武器", "g": "名词"}]}
        with patch("index.llm.complete", return_value=json.dumps(payload)) as complete:
            items, missing = _call_provider_with_retry(words)
        assert missing == 0
        assert items[0]["m"] == "武器"
        assert complete.call_count == 1

    def test_splits_long_text_into_chunks(self):
        """整章一次请求会被 504，必须分块后按原顺序合并。"""
        words = [f"w{i}" for i in range(5)]

        def fake_complete(prompt):
            chunk = [
                line.split(". ", 1)[1]
                for line in prompt.splitlines()
                if ". " in line and line.split(".", 1)[0].strip().isdigit()
            ]
            return json.dumps({"items": [{"i": i + 1, "m": f"含义-{w}"} for i, w in enumerate(chunk)]})

        with patch("index.llm.complete", side_effect=fake_complete) as complete:
            items, missing = _call_provider_with_retry(words, chunk_size=2)
        assert complete.call_count == 3  # 2 + 2 + 1
        assert missing == 0
        assert [i["m"] for i in items] == [f"含义-w{i}" for i in range(5)]

    def test_dedupe_expands_unique_results_back_to_full_text(self):
        words = ["Gallia", "est", "omnis", "est", "divisa"]  # est 出现两次

        def fake_complete(prompt):
            chunk = [
                line.split(". ", 1)[1]
                for line in prompt.splitlines()
                if ". " in line and line.split(".", 1)[0].strip().isdigit()
            ]
            return json.dumps({"items": [{"i": i + 1, "m": f"含义-{w}"} for i, w in enumerate(chunk)]})

        with override_settings(GLOSS_DEDUPE=True), \
             patch("index.llm.complete", side_effect=fake_complete) as complete:
            items, missing = _call_provider_with_retry(words)
        # 只请求了一块：4 个 unique 词
        assert complete.call_count == 1
        assert missing == 0
        assert [i["m"] for i in items] == ["含义-Gallia", "含义-est", "含义-omnis", "含义-est", "含义-divisa"]

    def test_dedupe_counts_repeated_words_as_missing(self):
        words = ["a", "b", "a"]
        items = [{"w": "a", "m": "甲", "g": ""}]  # b 没被标上
        with override_settings(GLOSS_DEDUPE=True):
            expanded, missing = _expand_unique(items, words)
        assert missing == 1  # 只有 b 缺失，重复出现的 a 不算
        assert [i["m"] for i in expanded] == ["甲", FAILED_MARK, "甲"]

    def test_retries_then_falls_back_on_error(self):
        with patch("index.llm.complete", side_effect=RuntimeError("429 quota")) as complete, \
             patch("index.views.time.sleep"):
            items, missing = _call_provider_with_retry(["a", "b"])
        assert complete.call_count == 3  # 初次 + 2 次重试
        assert missing == 2
        assert all(i["m"] == "解析失败" for i in items)


@pytest.mark.django_db
class TestGetOrCreateGloss:
    def test_empty_text_returns_empty(self):
        gloss_data, text_hash = get_or_create_gloss("")
        assert gloss_data == []
        assert text_hash == ""

    def test_cached_gloss_is_returned(self):
        GlossCache.objects.create(
            text_hash="abc123", gloss_data=[{"w": "test", "m": "测试", "g": "名词"}]
        )
        with patch("index.views.GlossCache.objects.filter") as mock_filter:
            mock_filter.return_value.first.return_value = GlossCache.objects.first()
            gloss_data, text_hash = get_or_create_gloss("test")
            assert gloss_data == [{"w": "test", "m": "测试", "g": "名词"}]

    def test_dictionary_gloss_is_cached(self):
        """默认路径：公开词典提供释义，完全不调用模型。"""
        entry = DictEntry(word_form="alpha", gloss_zh="第一", pos="形容词")
        with patch("index.views.dicts.lookup", return_value={"alpha": entry}):
            gloss_data, _ = get_or_create_gloss("alpha")
        assert gloss_data[0]["m"] == "第一"
        assert GlossCache.objects.count() == 1

    def test_model_is_not_called_by_default(self):
        """AI 只用于讲解，逐词标注默认不碰模型。"""
        with patch("index.views.dicts.lookup", return_value={}), \
             patch("index.views._call_provider_with_retry") as mock_call:
            get_or_create_gloss("alpha beta")
        assert mock_call.call_count == 0

    def test_ai_fills_missing_words_only_when_enabled(self):
        with patch("index.views.dicts.lookup", return_value={}), \
             patch("index.views._call_provider_with_retry") as mock_call:
            mock_call.return_value = ([{"w": "alpha", "m": "第一", "g": "形容词"}], 0)
            with override_settings(GLOSS_AI_WORDS=True):
                gloss_data, _ = get_or_create_gloss("alpha")
        assert mock_call.call_count == 1
        assert gloss_data[0]["m"] == "第一"

    def test_misaligned_result_is_not_cached(self):
        """错位结果写进缓存会永久污染，必须拒绝。"""
        with patch("index.views.dicts.lookup", return_value={}), \
             patch("index.views._call_provider_with_retry") as mock_call:
            mock_call.return_value = ([{"w": "alpha", "m": "", "g": ""}], 1)
            with override_settings(GLOSS_AI_WORDS=True):
                get_or_create_gloss("alpha")
        assert GlossCache.objects.count() == 0


class TestVerifyCaptcha:
    """后端必须校验 success + action + hostname，缺一项都可能被跨站/跨动作重放。"""

    def _call(self, token, payload, ok=True, exc=None, hostnames="latin-library.onrender.com"):
        import index.views as views

        fake_response = MagicMock()
        fake_response.ok = ok
        fake_response.json.return_value = payload
        with override_settings(
            TURNSTILE_SECRET="secret", TURNSTILE_HOSTNAMES=hostnames
        ), patch.object(views.requests, "post", side_effect=exc, return_value=fake_response) as post:
            result = views.verify_captcha(token)
            kwargs = post.call_args.kwargs if post.called else {}
        return result, kwargs

    def test_accepts_valid_token(self):
        (ok, reason), _ = self._call("t" * 20, {"success": True, "action": "gloss", "hostname": "latin-library.onrender.com"})
        assert (ok, reason) == (True, None)

    def test_rejects_missing_and_oversized_token(self):
        assert self._call("", {})[0] == (False, "invalid-token")
        assert self._call("x" * 2049, {})[0] == (False, "invalid-token")

    def test_rejects_siteverify_failure(self):
        (ok, reason), _ = self._call("t", {"success": False, "error-codes": ["timeout-or-duplicate"]})
        assert ok is False and reason == "timeout-or-duplicate"

    def test_rejects_action_mismatch(self):
        (ok, reason), _ = self._call("t", {"success": True, "action": "login", "hostname": "latin-library.onrender.com"})
        assert ok is False and reason == "action-mismatch"

    def test_rejects_hostname_mismatch(self):
        (ok, reason), _ = self._call("t", {"success": True, "action": "gloss", "hostname": "evil.example.com"})
        assert ok is False and reason == "hostname-mismatch"

    def test_rejects_on_timeout(self):
        (ok, reason), _ = self._call("t", {}, exc=RuntimeError("timeout"))
        assert ok is False and reason == "siteverify-error"

    def test_sends_secret_response_and_remoteip(self):
        from django.test import RequestFactory

        import index.views as views

        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"success": True, "action": "gloss", "hostname": "latin-library.onrender.com"}
        request = RequestFactory().get("/api/gloss", HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.1")
        with override_settings(TURNSTILE_SECRET="s3cret", TURNSTILE_HOSTNAMES="latin-library.onrender.com"), \
             patch.object(views.requests, "post", return_value=fake_response) as post:
            views.verify_captcha("tok", request=request)
        data = post.call_args.kwargs["data"]
        assert data["secret"] == "s3cret"
        assert data["response"] == "tok"
        assert data["remoteip"] == "1.2.3.4"
        assert post.call_args.kwargs["timeout"] == 10

    def test_rejects_when_hostnames_not_configured(self):
        (ok, reason), _ = self._call("t", {"success": True}, hostnames="")
        assert ok is False and reason == "server-misconfigured"


class TestLLMProvider:
    """provider 由环境变量决定，换模型不该改代码。"""

    def setup_method(self):
        llm.reset_provider()

    def test_gemini_provider_is_used_when_configured(self):
        with override_settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="k", LLM_MODEL=""), \
             patch("google.generativeai.GenerativeModel"):
            assert llm.get_provider().name == "gemini"

    def test_openai_compatible_requires_key_and_model(self):
        with override_settings(LLM_PROVIDER="openai_compatible", LLM_API_KEY="", LLM_MODEL="m"):
            with pytest.raises(llm.ProviderError):
                llm.get_provider()
        llm.reset_provider()
        with override_settings(LLM_PROVIDER="openai_compatible", LLM_API_KEY="k", LLM_MODEL=""):
            with pytest.raises(llm.ProviderError):
                llm.get_provider()

    def test_unknown_provider_raises(self):
        with override_settings(LLM_PROVIDER="nope"):
            with pytest.raises(llm.ProviderError):
                llm.get_provider()

    def test_openai_compatible_request_shape(self):
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"items":[]}'))]
        )
        with override_settings(
            LLM_PROVIDER="openai_compatible",
            LLM_API_KEY="k",
            LLM_MODEL="deepseek-chat",
            LLM_BASE_URL="https://api.example.com/v1",
            GLOSS_TIMEOUT=30,
        ), patch("openai.OpenAI", return_value=fake_client) as openai_ctor:
            provider = llm.get_provider()
            output = provider.complete("prompt")

        assert output == '{"items":[]}'
        assert openai_ctor.call_args.kwargs["base_url"] == "https://api.example.com/v1"
        assert openai_ctor.call_args.kwargs["timeout"] == 30
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "deepseek-chat"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["temperature"] == 0

    def test_complete_wraps_unexpected_errors(self):
        with override_settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="k", LLM_MODEL=""):
            llm.reset_provider()
            with patch.object(llm.GeminiProvider, "complete", side_effect=ValueError("boom")):
                with pytest.raises(llm.ProviderError):
                    llm.complete("prompt")


@pytest.mark.django_db
class TestApiExplain:
    def test_explain_generates_and_caches(self, rf=None):
        from django.test import RequestFactory

        request = RequestFactory().get("/api/explain", {"work_id": "1", "path": "p1"})
        with patch("index.views.Contents.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value = [type("S", (), {"text": "Gallia est omnis divisa."})()]
            with patch("index.llm.complete", return_value="这是凯撒《高卢战记》开篇。") as complete:
                response = views.api_explain(request)
        assert response.status_code == 200
        assert json.loads(response.content)["cached"] is False
        assert complete.call_count == 1

        # 第二次直接命中缓存，不再调用模型
        with patch("index.views.Contents.objects.filter") as mock_filter2:
            mock_filter2.return_value.order_by.return_value = [type("S", (), {"text": "Gallia est omnis divisa."})()]
            with patch("index.llm.complete", side_effect=AssertionError("不应再次调用模型")):
                response2 = views.api_explain(request)
        assert json.loads(response2.content)["cached"] is True

    def test_explain_returns_503_when_model_fails(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/api/explain", {"work_id": "1", "path": "p1"})
        with patch("index.views.Contents.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value = [type("S", (), {"text": "arma virumque cano."})()]
            with patch("index.llm.complete", side_effect=RuntimeError("quota")):
                response = views.api_explain(request)
        assert response.status_code == 503


class TestCheckRateLimit:
    def test_first_request_allows_and_sets_cache(self):
        fake_cache = {}
        with patch("index.views.cache") as mock_cache:
            mock_cache.get.return_value = 0
            mock_cache.set.side_effect = lambda k, v, timeout: fake_cache.update({k: v})

            assert check_rate_limit("test_key", 5, 60) is True
            mock_cache.set.assert_called_once_with("test_key", 1, timeout=60)

    def test_exceeded_limit_returns_false(self):
        with patch("index.views.cache") as mock_cache:
            mock_cache.get.return_value = 5
            assert check_rate_limit("test_key", 5, 60) is False
