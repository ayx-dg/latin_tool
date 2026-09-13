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

from .models import Works, Contents, GlossCache

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)
_model = genai.GenerativeModel('gemini-2.5-flash')


def check_rate_limit(key, limit, period):
    count = cache.get(key, 0)
    if count >= limit:
        return False
    if count == 0:
        cache.set(key, 1, timeout=period)
    else:
        cache.incr(key)
    return True


def verify_captcha(token):
    secret_key = settings.TURNSTILE_SECRET
    url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    response = requests.post(url, data={
        'secret': secret_key,
        'response': token,
    })
    return response.json().get('success', False)


def _extract_words(full_text):
    return re.findall(r'[^\W\d_]+', full_text, re.UNICODE)


def _build_gloss_prompt(full_text):
    words_only = _extract_words(full_text)
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
    return [{"w": w, "m": "解析失败", "g": ""} for w in words]


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


def _call_provider_with_retry(prompt, words):
    """带超时和退避重试地调用模型。返回 (items, missing)。"""
    timeout = float(getattr(settings, "GLOSS_TIMEOUT", 60))
    retries = int(getattr(settings, "GLOSS_RETRIES", 2))
    last_error = None

    for attempt in range(retries + 1):
        try:
            response = _model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
                request_options={"timeout": timeout},
            )
            items, missing = _align_items(words, json.loads(response.text))
            if missing == 0:
                return items, 0
            last_error = f"数量/索引对不上：{missing}/{len(words)} 个词缺失"
        except Exception as exc:  # noqa: BLE001 - 任何失败都要能优雅降级
            last_error = exc

        logger.warning("标注调用失败 (%s/%s): %s", attempt + 1, retries + 1, last_error)
        if attempt < retries:
            time.sleep(2 ** attempt)

    return _fallback_gloss(" ".join(words)) if words else [], len(words)


def get_or_create_gloss(full_text):
    if not full_text.strip():
        return [], ""
    text_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()
    cached = GlossCache.objects.filter(text_hash=text_hash).first()
    if cached:
        return cached.gloss_data, text_hash

    words = _extract_words(full_text)
    gloss_data, missing = _call_provider_with_retry(_build_gloss_prompt(full_text), words)

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
def api_get_gloss(request):
    if getattr(settings, "TURNSTILE_ENABLED", True):
        cf_token = request.GET.get('cf_token')
        if not cf_token or not verify_captcha(cf_token):
            return JsonResponse({'error': 'Captcha verification failed.'}, status=403)

    work_id = request.GET.get('work_id')
    path = request.GET.get('path')

    if not (work_id and path):
        return JsonResponse({'error': 'Missing parameters.'}, status=400)

    try:
        segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
        full_text = " ".join([s.text for s in segments])
        full_text = re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)
        gloss_data, _ = get_or_create_gloss(full_text)
        return JsonResponse({'status': 'success', 'work_id': work_id, 'path': path, 'gloss': gloss_data})
    except Exception as e:
        return JsonResponse({'error': f'Internal server error {str(e)}'}, status=500)
