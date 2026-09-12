import hashlib
import json
import logging
import re

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
Analyze the following Latin words and provide a JSON array.
Maintain the EXACT order and count of the input list.

Input List:
{indexed_text}

Return a JSON array where each object has:
"m": Chinese (Simplified) meaning,
"g": Grammar (Chinese).
Return ONLY the JSON array.
"""


def _fallback_gloss(full_text):
    words = _extract_words(full_text) or full_text.split()
    return [{"w": w, "m": "解析失败", "g": ""} for w in words]


def get_or_create_gloss(full_text):
    if not full_text.strip():
        return [], ""
    text_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()
    cached = GlossCache.objects.filter(text_hash=text_hash).first()
    if cached:
        return cached.gloss_data, text_hash
    try:
        prompt = _build_gloss_prompt(full_text)
        response = _model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        gloss_data = json.loads(response.text)
        GlossCache.objects.create(text_hash=text_hash, gloss_data=gloss_data)
        return gloss_data, text_hash
    except Exception as e:
        logger.exception("Gemini API call failed")
        return _fallback_gloss(full_text), text_hash


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
    })


def work_redirect(request, work_id):
    first_content = Contents.objects.filter(work_id=work_id).order_by('global_order').first()
    if first_content:
        return redirect('work_detail', work_id=work_id, path=first_content.path)
    return render(request, '404.html', {'message': '作品内容为空'})


# --- API ---

@csrf_exempt
def api_get_gloss(request):
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
