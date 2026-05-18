import hashlib
import json
import google.generativeai as genai
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .models import Works, Contents, GlossCache
import logging,re
#from ratelimit.decorators import ratelimit
# --- 配置区 ---
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')
logger = logging.getLogger(__name__)

from django.core.cache import cache

# --- 辅助函数：执行限流计数 ---
def _is_rate_limited(user_ip):
    """
    内部工具函数：执行双重限流检查
    """
    # 1. 全站 API 限流 (所有用户共用)
    if not check_rate_limit("limit_global_api_call", limit=60, period=60):
        return True
    
    # 2. 个人 API 限流 (按 IP)
    if not check_rate_limit(f"limit_ip_api_call_{user_ip}", limit=1, period=60):
        return True
    
    return False

def get_or_create_gloss(full_text, user_ip):
    """
    核心业务逻辑：负责数据库缓存校验与 Gemini API 交互
    """
    if not full_text.strip():
        return [], ""
    print('get or create')
    # 1. 计算哈希值
    text_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()

    # 2. 缓存查询
    cached = GlossCache.objects.filter(text_hash=text_hash).first()
    if cached:
        print('there is cache')
#        logger.debug(cached.gloss_data)
        return cached.gloss_data, text_hash
# --- 缓存未命中，准备调用 API，开始限流检查 ---
    #print('no cache - checking rate limits before API call')
    #if _is_rate_limited(user_ip):
        # 如果触发限流，抛出异常或返回特定标记
    #    raise PermissionError("Rate limit exceeded for Gemini API")
    print('no cache')

    try:
# 使用正则提取所有纯单词（排除标点）
        # [^\W\d_] 表示：是非特殊字符，且不是数字，且不是下划线（即：字母）

        words_only = re.findall(r'[^\W\d_]+', full_text, re.UNICODE)

    # 构造带索引的列表，明确告诉 Gemini 只需要翻译这些
    # 格式如：1. Scribere, 2. de, 3. clementia...
        indexed_text = "\n".join([f"{i+1}. {word}" for i, word in enumerate(words_only)])
    

        prompt = f"""
    Analyze the following Latin words and provide a JSON array.
    Maintain the EXACT order and count of the input list.

    Input List:
    {indexed_text}

    Return a JSON array where each object has:
    "m": Chinese (Simplified) meaning,
    "g": Grammar (Chinese).
    Return ONLY the JSON array.
    """

    except Exception as e:
        print('error occured while creating prompt')
        print(e)
        # 失败兜底
        fallback = [{"w": w, "m": "解析失败", "g": ""} for w in full_text.split()]
        return fallback, text_hash
        

    try:
        print('trying')
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
            #request_options={"timeout": 20}
        )
        gloss_data = json.loads(response.text)
        print(response)        
        # 4. 存入缓存
        GlossCache.objects.create(text_hash=text_hash, gloss_data=gloss_data)
#        logger.debug(gloss_data)
        return gloss_data, text_hash
    except Exception as e:
        print('fail')
        print(f"Gemini API Error: {e}")
        # 失败兜底
        fallback = [{"w": w, "m": "解析失败", "g": ""} for w in full_text.split()]
        return fallback, text_hash

# --- 页面视图 ---

def work_list(request):
    works = Works.objects.all()
    return render(request, 'list.html', {'works': works})

def work_detail(request, work_id, path):
    work = get_object_or_404(Works, id=work_id)
    
    # 获取当前 path 的内容
    segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
    full_text = " ".join([s.text for s in segments])
    full_text = re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)
    # 翻页逻辑
    from django.db.models import Min
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

    # 改成json ， 只返回work detail
    return render(request, 'detail.html', {
        'work': work,
        'full_text': full_text,
        'prev_path': prev_path,
        'next_path': next_path,
        'current_path': path,
        'work_id': work_id
    })

def work_redirect(request, work_id):
    first_content = Contents.objects.filter(work_id=work_id).order_by('global_order').first()
    if first_content:
        return redirect('work_detail', work_id=work_id, path=first_content.path)
    return render(request, '404.html', {'message': '作品内容为空'})

import requests

def verify_captcha(token):
    """
    向 Cloudflare 发起验证请求
    """
    secret_key = settings.TURNSTILE_SECRET # 替换为你的私钥
    url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    
    response = requests.post(url, data={
        'secret': secret_key,
        'response': token
    })
    result = response.json()
    return result.get('success', False)

# --- API 接口 ---
@csrf_exempt
def api_get_gloss(request):
    """
    API 接口：利用现有 get_or_create_gloss 函数
    通过 work_id 和 path 定位原文，获取标注
    """
    # 1. 优先获取验证码 Token
    cf_token = request.GET.get('cf_token')
    
    # 2. 校验验证码（这一步不走 Redis，不走 Gemini，最先执行）
    if not cf_token or not verify_captcha(cf_token):
        return JsonResponse({'error': 'Captcha verification failed.'}, status=403)

    # --- 验证码通过后，再执行后续的限流、数据库查询和 API 调用 ---
    # 获取参数
    work_id = request.GET.get('work_id')
    path = request.GET.get('path')

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        user_ip = x_forwarded_for.split(',')[0]
    else:
        user_ip = request.META.get('REMOTE_ADDR')

    # 1. 核心逻辑：必须有定位原文的依据
    if not (work_id and path):
        return JsonResponse({
            'error': 'Missing parameters. Both work_id and path are required to locate text.'
        }, status=400)

    try:
        # 2. 从 Contents 表中根据 work_id 和 path 获取原文
        # 必须按 global_order 排序，否则拼出来的句子是乱序的，Gemini 无法理解语义
        segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
        full_text = " ".join([s.text for s in segments])
        full_text = re.sub(r'(?<=\w)\s+(?=[.,!?;:])', '', full_text)
    

        # 3. 调用你已有的函数
    # 该函数内部已经处理了：检查 GlossCache -> 调用 Gemini -> 存入 GlossCache
    # 关键：调用时把 user_ip 传进去
        gloss_data, _ = get_or_create_gloss(full_text, user_ip=user_ip)

        #logger.debug(gloss_data)
        # 4. 返回 JSON
        return JsonResponse({
            'status': 'success',
            'work_id': work_id,
            'path': path,
            'gloss': gloss_data
        })

    except PermissionError as e:
        # 捕获限流异常
        return JsonResponse({'error': str(e)}, status=429)
    except Exception as e:
        return JsonResponse({'error': f'Internal server error {str(e)}'}, status=500)

from django.core.cache import cache  # 导入 Django 缓存
import time

# --- 限流工具函数 ---
def check_rate_limit(key, limit, period):
    """
    key: 缓存的键名
    limit: 周期内允许的最大次数
    period: 周期时长（秒）
    """
    # 获取当前计数值
    count = cache.get(key, 0)
    if count >= limit:
        return False
    
    if count == 0:
        # 第一次访问，设置初始值并定义过期时间
        cache.set(key, 1, timeout=period)
    else:
        # 原子性自增
        cache.incr(key)
    return True

