import pytest
from unittest.mock import patch

import django
from index.models import GlossCache
from index.views import (
    _extract_words,
    _build_gloss_prompt,
    _fallback_gloss,
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

    def test_prompt_requests_json_array(self):
        prompt = _build_gloss_prompt("arma")
        assert "JSON array" in prompt or "Return ONLY the JSON array" in prompt


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
