import hashlib
import json
import google.generativeai as genai
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .models import Works, Contents, GlossCache
import logging
# --- 配置区 ---
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')
logger = logging.getLogger(__name__)

def get_or_create_gloss(full_text):
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
    print('no cache')
    # 3. 调用 API
    prompt = f"""
    Analyze the following Latin text and provide a word-for-word gloss in a JSON array. 
    Each item: "w": Latin word, "m": Chinese meaning, "g": Grammar (Chinese). 
    Text: {full_text}
    Return ONLY JSON array.
    """

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
    
    # 【修改重点】计算当前页面的哈希值，交给前端 JS
    current_hash = hashlib.md5(full_text.encode('utf-8')).hexdigest()

    # 翻页逻辑
    from django.db.models import Min
    all_paths = list(Contents.objects.filter(work_id=work_id)
                 .values('path')
                 .annotate(min_order=Min('global_order'))
                 .order_by('min_order')
                 .values_list('path', flat=True))
    
    print(all_paths)
    try:
        current_index = all_paths.index(path)
        prev_path = all_paths[current_index - 1] if current_index > 0 else None
        next_path = all_paths[current_index + 1] if current_index < len(all_paths) - 1 else None
    except ValueError:
        prev_path = next_path = None

    # 改成json ， 只返回work detail
    return render(request, 'detail.html', {
        'work': work,
        'segments': segments,
        'current_hash': current_hash,  # 传递给前端
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

# --- API 接口 ---
@csrf_exempt
def api_get_gloss(request):
    """
    API 接口：利用现有 get_or_create_gloss 函数
    通过 work_id 和 path 定位原文，获取标注
    """
    # 获取参数
    work_id = request.GET.get('work_id')
    path = request.GET.get('path')

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
    

        # 3. 调用你已有的函数
        # 该函数内部已经处理了：检查 GlossCache -> 调用 Gemini -> 存入 GlossCache
        gloss_data, _ = get_or_create_gloss(full_text)

        #logger.debug(gloss_data)
        # 4. 返回 JSON
        return JsonResponse({
            'status': 'success',
            'work_id': work_id,
            'path': path,
            'gloss': gloss_data
        })

    except Exception as e:
        # 记录错误日志
        print(f"API Error: {str(e)}")
        return JsonResponse({'error': 'Internal server error processing gloss'}, status=500)
