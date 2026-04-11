import hashlib
import json
import os
import google.generativeai as genai
from django.shortcuts import render, get_object_or_404
from .models import Works, Contents, GlossCache
from django.conf import settings # 导入 settings

# 配置 Gemini
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

def get_or_create_gloss(latin_text):
    # 1. 计算哈希值
    text_hash = hashlib.md5(latin_text.encode('utf-8')).hexdigest()
    
    # 2. 尝试从数据库获取
    cached = GlossCache.objects.filter(text_hash=text_hash).first()
    if cached:
        return cached.gloss_data

    # 3. 缓存未命中，调用 Gemini
    prompt = f"""
    You are a Latin linguistics expert. Analyze the Latin text and provide a word-for-word gloss in JSON format.
    Each item in the list must have:
    "w": the original Latin word
    "m": concise Chinese meaning
    "g": grammatical analysis (case, number, gender, tense, person, etc. in Chinese)
    
    Text: {latin_text}
    Return ONLY the JSON array.
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        gloss_result = json.loads(response.text)
        
        # 4. 存入数据库供下次使用
        GlossCache.objects.create(text_hash=text_hash, gloss_data=gloss_result)
        return gloss_result
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return None
# views.py
from django.shortcuts import render, get_object_or_404
from .models import Works, Contents

def work_list(request):
    works = Works.objects.all()
    return render(request, 'list.html', {'works': works})

def work_detail(request, work_id, path):
    work = get_object_or_404(Works, id=work_id)
    
    # 获取当前 path 的所有行
    segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
    
    # 获取全文用于标注（也可以逐句标注，这里演示合并标注）
    full_text = " ".join([s.text for s in segments])
    gloss_data = get_or_create_gloss(full_text)

    # 获取该作品的所有唯一 path，按 global_order 排序，用于翻页
    all_paths = list(Contents.objects.filter(work_id=work_id)
                     .values_list('path', flat=True)
                     .distinct()
                     .order_by('global_order'))
    
    # 计算索引以获取上一个和下一个 path
    current_index = all_paths.index(path)
    prev_path = all_paths[current_index - 1] if current_index > 0 else None
    next_path = all_paths[current_index + 1] if current_index < len(all_paths) - 1 else None

    return render(request, 'detail.html', {
        'work': work,
        'segments': segments,
        'gloss_data': gloss_data,
        'prev_path': prev_path,
        'next_path': next_path,
        'current_path': path
    })

from django.shortcuts import redirect

def work_redirect(request, work_id):
    # 找到该作品 global_order 最小（即最开头）的那一行
    first_content = Contents.objects.filter(work_id=work_id).order_by('global_order').first()
    if first_content:
        # 重定向到带 path 的完整 URL
        return redirect('work_detail', work_id=work_id, path=first_content.path)
    return render(request, '404.html', {'message': '作品内容为空'})

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

@csrf_exempt # 方便开发调试
def api_get_gloss(request):
    # 允许 GET 请求带文本，或 POST 请求带文本
    text = request.GET.get('text') or request.POST.get('text')
    if not text:
        return JsonResponse({'error': 'No text'}, status=400)
    
    # 调用你之前的 get_or_create_gloss 逻辑
    gloss_data = get_or_create_gloss(text)
    
    return JsonResponse({'gloss': gloss_data})
