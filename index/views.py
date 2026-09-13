import hashlib
import json
import logging
import re
import time

import google.generativeai as genai
import requests
from django.conf import settings
from django.core.cache import cache
from django.db.models import Min
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.csrf import csrf_exempt

from . import dicts, llm
from .models import ChapterNote, Works, Contents, DictEntry, GlossCache

logger = logging.getLogger(__name__)

# 兼容旧引用：探针脚本和老代码可能会用到 _model
_model = None
if getattr(settings, "GEMINI_API_KEY", ""):
    genai.configure(api_key=settings.GEMINI_API_KEY)
    _model = genai.GenerativeModel(
        getattr(settings, "LLM_GEMINI_MODEL", "") or llm.DEFAULT_GEMINI_MODEL
    )


def check_rate_limit(key, limit, period):
    count = cache.get(key, 0)
    if count >= limit:
        return False
    if count == 0:
        cache.set(key, 1, timeout=period)
    else:
        cache.incr(key)
    return True


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
GLOSS_ACTION = "gloss"
# 降级占位文案。统计"缺失"时必须把带这个标记的词也算进去，
# 否则整章失败会被误判为成功。
FAILED_MARK = "解析失败"


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def verify_captcha(token, request=None, action=GLOSS_ACTION):
    """规范化的 Turnstile 校验。返回 (ok, reason)。

    除了 success，还必须校验 action 和 hostname，否则拿到别的站点/别的动作签发的
    token 也能通过。token 单次有效，重复提交会被 Cloudflare 拒绝（timeout-or-duplicate）。
    """
    secret_key = getattr(settings, "TURNSTILE_SECRET", "")
    hostnames = {
        h.strip()
        for h in getattr(settings, "TURNSTILE_HOSTNAMES", "").split(",")
        if h.strip()
    }

    if not isinstance(token, str) or not (0 < len(token) <= 2048):
        return False, "invalid-token"
    if not secret_key or not hostnames:
        logger.error("TURNSTILE_SECRET 或 TURNSTILE_HOSTNAMES 未配置")
        return False, "server-misconfigured"

    try:
        response = requests.post(
            TURNSTILE_VERIFY_URL,
            data={
                "secret": secret_key,
                "response": token,
                "remoteip": _client_ip(request) or "",
            },
            timeout=10,
        )
        if not response.ok:
            return False, f"siteverify-{response.status_code}"
        result = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("siteverify 请求失败: %s", exc)
        return False, "siteverify-error"

    if not result.get("success"):
        codes = result.get("error-codes") or ["unknown"]
        logger.warning("Turnstile 校验失败: %s", codes)
        return False, codes[0]

    if action and result.get("action") not in (None, "", action):
        return False, "action-mismatch"
    if result.get("hostname") and result["hostname"] not in hostnames:
        logger.warning("Turnstile hostname 不符: %s", result.get("hostname"))
        return False, "hostname-mismatch"

    return True, None


def _extract_words(full_text):
    return re.findall(r'[^\W\d_]+', full_text, re.UNICODE)


def _build_gloss_prompt(full_text):
    # 既接受整段文本，也接受已经切好的词表（分块时传列表）
    words_only = full_text if isinstance(full_text, list) else _extract_words(full_text)
    indexed_text = "\n".join([f"{i+1}. {word}" for i, word in enumerate(words_only)])
    return f"""
Analyze the following Latin words and provide a JSON object.
Maintain the EXACT order and count of the input list.

Input List:
{indexed_text}

Return a JSON object with a single key "items", whose value is an array where each object has:
"i": the index number shown above (1-based),
"m": Chinese (Simplified) meaning,
"g": Grammar (Chinese).
Return exactly {len(words_only)} objects. Return ONLY the JSON object.
"""


def _fallback_gloss(full_text):
    words = _extract_words(full_text) or full_text.split()
    return [{"w": w, "m": FAILED_MARK, "g": ""} for w in words]


def _missing_count(items):
    """没拿到释义的词数：空值或降级占位都算缺失。"""
    return sum(1 for item in items if not item.get("m") or item["m"] == FAILED_MARK)


def _parse_items(raw):
    """兼容模型返回的几种形态：{"items": [...]} / 裸数组 / 其它键名。"""
    if isinstance(raw, dict):
        for key in ("items", "gloss", "data", "words", "result"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        return []
    return raw if isinstance(raw, list) else []


def _align_items(words, raw):
    """把模型返回对齐回词表。返回 (items, missing)。

    前端按 \p{L}+ 顺序消费数组，所以数量/顺序必须和 _extract_words 完全一致，
    否则整页标注会错位。
    """
    result = [{"w": w, "m": "", "g": ""} for w in words]
    items = _parse_items(raw)
    if not items:
        return result, len(words)

    by_index = {}
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        idx = item.get("i")
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = None
        if idx is None or not (1 <= idx <= len(words)):
            # 模型没给索引或索引越界：退化为按出现顺序对齐
            by_index.setdefault(pos + 1, item)
        else:
            by_index[idx] = item

    missing = 0
    for i, word in enumerate(words, start=1):
        item = by_index.get(i)
        if item and (item.get("m") or item.get("g")):
            result[i - 1] = {"w": word, "m": item.get("m", ""), "g": item.get("g", "")}
        else:
            missing += 1
    return result, missing


def _gloss_words_with_retry(words):
    """对一小批词调用模型，带退避重试。返回 (items, missing)。"""
    if not words:
        return [], 0

    retries = int(getattr(settings, "GLOSS_RETRIES", 2))
    last_error = None

    for attempt in range(retries + 1):
        try:
            raw = llm.complete(_build_gloss_prompt(words))
            items, missing = _align_items(words, json.loads(raw))
            if missing == 0:
                return items, 0
            last_error = f"数量/索引对不上：{missing}/{len(words)} 个词缺失"
        except Exception as exc:  # noqa: BLE001 - 任何失败都要能优雅降级
            last_error = exc

        logger.warning("标注调用失败 (%s/%s): %s", attempt + 1, retries + 1, last_error)
        if attempt < retries:
            time.sleep(2 ** attempt)

    return [{"w": w, "m": FAILED_MARK, "g": ""} for w in words], len(words)


def _chunked_gloss(words, chunk_size):
    """长章节分块请求再合并：整章一次请求会被判 504 超时。"""
    if chunk_size <= 0 or len(words) <= chunk_size:
        return _gloss_words_with_retry(words)

    merged = []
    total_missing = 0
    for start in range(0, len(words), chunk_size):
        chunk = words[start:start + chunk_size]
        items, missing = _gloss_words_with_retry(chunk)
        merged.extend(items)
        total_missing += missing
        logger.info("分块标注 %s-%s / %s（缺失 %s）", start + 1, start + len(chunk), len(words), missing)
    return merged, total_missing


def _expand_unique(unique_items, words):
    """把「只标 unique 词形」的结果按原文顺序展开。"""
    lookup = {}
    for item in unique_items:
        if item.get("w") and (item.get("m") or item.get("g")):
            lookup[item["w"]] = (item.get("m", ""), item.get("g", ""))

    expanded = []
    missing = 0
    for word in words:
        if word in lookup:
            meaning, grammar = lookup[word]
            expanded.append({"w": word, "m": meaning, "g": grammar})
        else:
            expanded.append({"w": word, "m": FAILED_MARK, "g": ""})
            missing += 1
    return expanded, missing


def _call_provider_with_retry(words, chunk_size=None):
    """调用模型拿到与 words 等长、同序的标注。返回 (items, missing)。"""
    if chunk_size is None:
        chunk_size = int(getattr(settings, "GLOSS_CHUNK_WORDS", 150))
    if not words:
        return [], 0

    # 拉丁文重复词极多（est / et / in …），只标 unique 词形可省 40-60% 输出 token。
    # 代价是丢失上下文，同一个词形的多种形态会共用释义。
    if getattr(settings, "GLOSS_DEDUPE", False):
        unique = list(dict.fromkeys(words))
        unique_items, _missing = _chunked_gloss(unique, chunk_size)
        logger.info("去重标注: %s 词 -> %s 个 unique", len(words), len(unique))
        return _expand_unique(unique_items, words)

    return _chunked_gloss(words, chunk_size)


def _build_explain_prompt(full_text):
    return f"""你是拉丁语教师，请用中文讲解下面这段拉丁文（面向初学者）：

1. 用两三句话概括大意（中译）
2. 指出 2-3 个语法/词法难点（如夺格、分词、虚拟式）
3. 挑 1-2 个难句逐词说明

要求：中文回答，条理清晰，不要重复原文。

原文：
{full_text[:4000]}
"""


def get_or_create_gloss(full_text):
    """逐词标注：公开词典优先（免费、即时、无需人机验证）。

    只有 GLOSS_AI_WORDS=true 时才对词典没收录的词调用模型；
    AI 的主要用途是整章讲解（api_explain）。
    """
    if not full_text.strip():
        return [], ""
    text_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()
    cached = GlossCache.objects.filter(text_hash=text_hash).first()
    if cached:
        return cached.gloss_data, text_hash

    words = _extract_words(full_text)
    gloss_data = dicts.gloss_words(words)
    missing = _missing_count(gloss_data)

    if missing and getattr(settings, "GLOSS_AI_WORDS", False):
        ai_items, _ai_missing = _call_provider_with_retry(words)
        merged = []
        for item, ai in zip(gloss_data, ai_items):
            merged.append(ai if (not item.get("m") and ai.get("m")) else item)
        gloss_data = merged
        missing = _missing_count(gloss_data)

    if missing:
        # 错位的结果绝不能写进缓存，否则以后永远读到错误标注
        logger.warning("标注不完整，跳过缓存: hash=%s missing=%s", text_hash, missing)
    else:
        GlossCache.objects.create(text_hash=text_hash, gloss_data=gloss_data)
    return gloss_data, text_hash


# --- Page Views ---

def work_list(request):
    works = Works.objects.all()
    return render(request, 'list.html', {'works': works})


def work_detail(request, work_id, path):
    work = get_object_or_404(Works, id=work_id)
    segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
    full_text = " ".join([s.text for s in segments])
    full_text = re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)

    all_paths = list(Contents.objects.filter(work_id=work_id)
                     .values('path')
                     .annotate(min_order=Min('global_order'))
                     .order_by('min_order')
                     .values_list('path', flat=True))

    try:
        current_index = all_paths.index(path)
        prev_path = all_paths[current_index - 1] if current_index > 0 else None
        next_path = all_paths[current_index + 1] if current_index < len(all_paths) - 1 else None
    except ValueError:
        prev_path = next_path = None

    return render(request, 'detail.html', {
        'work': work,
        'full_text': full_text,
        'prev_path': prev_path,
        'next_path': next_path,
        'current_path': path,
        'work_id': work_id,
        'turnstile_enabled': getattr(settings, 'TURNSTILE_ENABLED', True),
        'turnstile_sitekey': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
    })


def work_redirect(request, work_id):
    first_content = Contents.objects.filter(work_id=work_id).order_by('global_order').first()
    if first_content:
        return redirect('work_detail', work_id=work_id, path=first_content.path)
    return render(request, '404.html', {'message': '作品内容为空'})


# --- API ---

@csrf_exempt
def api_explain(request):
    """整章 AI 讲解：一次调用，结果永久缓存。"""
    work_id = request.GET.get('work_id')
    path = request.GET.get('path')

    if not (work_id and path):
        return JsonResponse({'error': 'Missing parameters.'}, status=400)

    segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
    full_text = " ".join([s.text for s in segments])
    full_text = re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)
    if not full_text.strip():
        return JsonResponse({'error': '章节为空。'}, status=404)

    text_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()
    note = ChapterNote.objects.filter(work_id=work_id, text_hash=text_hash).first()
    if note:
        return JsonResponse({'status': 'success', 'note': note.note, 'cached': True, 'provider': note.provider})

    try:
        answer = llm.complete(_build_explain_prompt(full_text), json_mode=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("生成讲解失败")
        return JsonResponse({'error': f'讲解服务暂时不可用：{exc}'}, status=503)

    ChapterNote.objects.create(
        work_id=work_id, path=path, text_hash=text_hash,
        note=answer, provider=llm.get_last_provider(),
    )
    return JsonResponse({'status': 'success', 'note': answer, 'cached': False, 'provider': llm.get_last_provider()})


@csrf_exempt
def api_get_gloss(request):
    if getattr(settings, "TURNSTILE_ENABLED", True):
        cf_token = request.GET.get('cf_token')
        ok, reason = verify_captcha(cf_token, request=request)
        if not ok:
            return JsonResponse({'error': '人机验证未通过，请刷新页面重试。', 'reason': reason}, status=403)

    work_id = request.GET.get('work_id')
    path = request.GET.get('path')

    if not (work_id and path):
        return JsonResponse({'error': 'Missing parameters.'}, status=400)

    try:
        segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
        full_text = " ".join([s.text for s in segments])
        full_text = re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)
        text_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()
        was_cached = GlossCache.objects.filter(text_hash=text_hash).exists()

        gloss_data, _ = get_or_create_gloss(full_text)
        missing = _missing_count(gloss_data)
        return JsonResponse({
            'status': 'success' if missing == 0 else 'partial',
            'work_id': work_id,
            'path': path,
            'gloss': gloss_data,
            'missing': missing,
            'total': len(gloss_data),
            'cached': was_cached,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("生成标注失败")
        return JsonResponse({'error': '标注服务暂时不可用，请稍后重试。'}, status=500)
