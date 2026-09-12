import json

import pytest
from unittest.mock import MagicMock, patch

import django
from index.models import GlossCache
from index.views import (
    _extract_words,
    _build_gloss_prompt,
    _fallback_gloss,
    _parse_items,
    _align_items,
    _call_provider_with_retry,
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
        fake_model = MagicMock()
        fake_model.generate_content.return_value = MagicMock(text=json.dumps(payload))
        with patch("index.views._model", fake_model):
            items, missing = _call_provider_with_retry("prompt", words)
        assert missing == 0
        assert items[0]["m"] == "武器"
        assert fake_model.generate_content.call_count == 1

    def test_retries_then_falls_back_on_error(self):
        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("429 quota")
        with patch("index.views._model", fake_model), patch("index.views.time.sleep"):
            items, missing = _call_provider_with_retry("prompt", ["a", "b"])
        assert fake_model.generate_content.call_count == 3  # 初次 + 2 次重试
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

    def test_misaligned_result_is_not_cached(self):
        """错位结果写进缓存会永久污染，必须拒绝。"""
        with patch("index.views._call_provider_with_retry") as mock_call:
            mock_call.return_value = ([{"w": "alpha", "m": "", "g": ""}], 1)
            get_or_create_gloss("alpha beta")
        assert GlossCache.objects.count() == 0

    def test_aligned_result_is_cached(self):
        with patch("index.views._call_provider_with_retry") as mock_call:
            mock_call.return_value = ([{"w": "alpha", "m": "第一", "g": "形容词"}], 0)
            gloss_data, _ = get_or_create_gloss("alpha")
        assert gloss_data[0]["m"] == "第一"
        assert GlossCache.objects.count() == 1


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
