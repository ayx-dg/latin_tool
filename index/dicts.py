"""公开词典查询层（无需 API key、无需人机验证）。

数据源：英文维基词典（Wiktionary）的拉丁语章节，取
- 词形还原：`{{inflection of|la|LEMMA|...}}` → lemma + 形态（格/数/时态…）
- 词条释义：`#` 开头的 gloss 行

结果落库到 `DictEntry`，同一个词形只查一次；中文释义由离线脚本
`scripts/fill_dict_zh.py` 用 AI 批量补（不在在线请求路径里）。
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import unicodedata
import urllib.request
from typing import Dict, Iterable, List

from django.conf import settings
from django.utils import timezone

from .models import DictEntry

logger = logging.getLogger(__name__)

WIKTIONARY_API = "https://en.wiktionary.org/w/api.php"
USER_AGENT = "latin-tool/0.1 (educational Latin reader; contact: local)"
BATCH_SIZE = 40
REQUEST_TIMEOUT = 20


class DictionaryError(Exception):
    pass


def _latin_section(wikitext: str) -> str:
    if not wikitext:
        return ""
    match = re.search(r"==\s*Latin\s*==(.*?)(?=\n==[^=]|\Z)", wikitext, re.S)
    return match.group(1) if match else ""


def _strip_templates(text: str) -> str:
    """把 {{...}} 简单摊平，够看就行（不是完整 wikitext 解析器）。"""
    text = re.sub(r"\{\{IPA\|[^}]*\}\}", "", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_inflection(section: str):
    """从 {{inflection of|la|LEMMA|...}} 里取 lemma 和形态描述。"""
    match = re.search(r"\{\{\s*inflection of\s*\|la\|([^|}]+)\|?(.*?)\}\}", section, re.S)
    if not match:
        return "", ""
    lemma = match.group(1).strip()
    rest = match.group(2)
    parts = [p.strip() for p in re.split(r"[|;]", rest) if p.strip()]
    # 去掉空段和纯标点，保留 nom/abl/f/s 之类
    morph_parts = [p for p in parts if re.fullmatch(r"[a-zA-Z/·]+", p)]
    return lemma, " ".join(morph_parts[:6])


POS_HEADERS = (
    "Noun", "Proper noun", "Verb", "Adjective", "Participle", "Adverb", "Pronoun",
    "Preposition", "Conjunction", "Numeral", "Interjection", "Particle",
    "Determiner", "Phrase", "Suffix", "Prefix", "Numeral noun",
)


def _sections(section: str):
    """切出 ===标题=== 段落，yield (标题, 正文)。"""
    parts = re.split(r"\n===\s*([^=\n]+?)\s*===\n", section)
    for i in range(1, len(parts), 2):
        yield parts[i].strip(), parts[i + 1]


def _parse_pos_and_gloss(section: str):
    """只在词性段落里取释义，避免抓到 Etymology / Pronunciation。"""
    fallback = ("", "")
    for header, body in _sections(section):
        if header in POS_HEADERS:
            gloss = _parse_gloss(body)
            if gloss:
                return header, gloss
            fallback = fallback or (header, "")
    for header, body in _sections(section):
        gloss = _parse_gloss(body)
        if gloss:
            return (header, gloss) if header in POS_HEADERS else ("", gloss)
    return fallback[0] or "", _parse_gloss(section)


def _parse_gloss(section: str) -> str:
    """取第一条非模板、非例句的释义。"""
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("#") or line.startswith("#:"):
            continue
        cleaned = _strip_templates(line.lstrip("#*").strip())
        if not cleaned or "inflection of" in cleaned.lower():
            continue
        # 跳过引文/参考文献行（"Tacitus, Gemanica, chapter 1 (Oxford…)" 之类）
        if re.search(r"(Oxford|chapter|References|ISBN|\(\d{4}\)|, [A-Z][a-z]+, [A-Z])", cleaned):
            continue
        if len(cleaned) > 160:
            continue
        return cleaned
    return ""


def _fold(text: str) -> str:
    """去掉长音符等附加符号：dīvīsus -> divisus（用来兜底查词条）。"""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def parse_entry(wikitext: str) -> dict:
    section = _latin_section(wikitext)
    if not section:
        return {}
    lemma, morph = _parse_inflection(section)
    pos, gloss = _parse_pos_and_gloss(section)
    return {
        "lemma": lemma,
        "morph": morph,
        "pos": pos,
        "gloss_en": gloss,
    }


def fetch_from_wiktionary(words: Iterable[str]) -> Dict[str, dict]:
    """批量取词条的 wikitext 并解析。一次最多 BATCH_SIZE 个词。"""
    words = [w for w in words if w]
    if not words:
        return {}

    result: Dict[str, dict] = {}
    for start in range(0, len(words), BATCH_SIZE):
        chunk = words[start:start + BATCH_SIZE]
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": "|".join(chunk),
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
        url = f"{WIKTIONARY_API}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                payload = json.load(response)
        except Exception as exc:  # noqa: BLE001 - 词典是可选增强，失败只降级
            logger.warning("Wiktionary 请求失败: %s", exc)
            continue

        pages = payload.get("query", {}).get("pages", []) or []
        by_title = {}
        for page in pages:
            if page.get("missing"):
                continue
            revisions = page.get("revisions") or []
            content = ""
            if revisions:
                slots = revisions[0].get("slots", {})
                content = (slots.get("main") or {}).get("content", "")
            if content:
                by_title[page.get("title", "").lower()] = parse_entry(content)

        for word in chunk:
            parsed = by_title.get(word.lower()) or by_title.get(word)
            if parsed:
                result[word] = parsed
        time.sleep(0.2)  # 对公共 API 客气一点
    return result


def _fill_from_lemma(fetched: Dict[str, dict]) -> None:
    """屈折形式（divisa / partes）本身没有释义，回溯到原形拿。"""
    candidates = set()
    for parsed in fetched.values():
        lemma = parsed.get("lemma")
        if lemma and not parsed.get("gloss_en"):
            candidates.add(lemma)
            folded = _fold(lemma)
            if folded != lemma:
                candidates.add(folded)
    if not candidates:
        return
    lemma_data = fetch_from_wiktionary(sorted(candidates))
    for parsed in fetched.values():
        if parsed.get("gloss_en") or not parsed.get("lemma"):
            continue
        lemma_entry = lemma_data.get(parsed["lemma"]) or lemma_data.get(_fold(parsed["lemma"]))
        if not lemma_entry:
            lowered = {k.lower(): v for k, v in lemma_data.items()}
            lemma_entry = lowered.get(_fold(parsed["lemma"]).lower())
        if lemma_entry and lemma_entry.get("gloss_en"):
            parsed["gloss_en"] = lemma_entry["gloss_en"]
            parsed["pos"] = parsed["pos"] or lemma_entry.get("pos", "")


def lookup(words: List[str]) -> Dict[str, DictEntry]:
    """先查库，缺的再去公开词典取并落库。返回 {word: DictEntry}。"""
    if not words:
        return {}

    wanted = list(dict.fromkeys(words))
    found = {
        entry.word_form: entry
        for entry in DictEntry.objects.filter(word_form__in=wanted)
    }

    missing = [w for w in wanted if w not in found]
    if not missing:
        return found

    fetched = fetch_from_wiktionary(missing)
    _fill_from_lemma(fetched)
    now = timezone.now()
    to_create = []
    for word, parsed in fetched.items():
        if not parsed or not (parsed.get("gloss_en") or parsed.get("lemma")):
            continue
        entry = DictEntry(
            word_form=word,
            lemma=parsed.get("lemma", ""),
            pos=parsed.get("pos", ""),
            morph=parsed.get("morph", ""),
            gloss_en=parsed.get("gloss_en", ""),
            source="wiktionary",
        )
        to_create.append(entry)
        found[word] = entry

    if to_create:
        try:
            DictEntry.objects.bulk_create(to_create, ignore_conflicts=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("词典落库失败: %s", exc)
            # 落库失败也要让本次请求能用
            for entry in to_create:
                found.setdefault(entry.word_form, entry)

    # 没查到的词：记一条空条目，避免每章重复打 API
    if getattr(settings, "DICT_REMEMBER_MISS", True):
        misses = [w for w in missing if w not in found]
        if misses:
            DictEntry.objects.bulk_create(
                [DictEntry(word_form=w, source="miss") for w in misses],
                ignore_conflicts=True,
            )
    logger.info("词典查询: %s 词，新收录 %s，未收录 %s", len(wanted), len(to_create),
                len([w for w in missing if w not in found]))
    return found


def gloss_words(words: List[str]) -> List[dict]:
    """逐词标注：优先中文，其次英文释义；形态信息合并进 g。"""
    if not words:
        return []
    entries = lookup(words)
    items = []
    for word in words:
        entry = entries.get(word)
        if entry is None:
            items.append({"w": word, "m": "", "g": "", "src": ""})
            continue
        meaning = entry.gloss_zh or entry.gloss_en
        grammar = " ".join(x for x in [entry.pos, entry.morph] if x).strip()
        if entry.lemma and entry.lemma != word:
            grammar = f"{grammar} ← {entry.lemma}".strip()
        items.append({
            "w": word,
            "m": meaning,
            "g": grammar,
            "src": entry.source,
        })
    return items
