from django.shortcuts import render
def index(request):
    return render(request, 'index.html')

# views.py
from django.shortcuts import render, get_object_or_404
from index.models import Works, Contents

def work_list(request):
    works = Works.objects.all()
    return render(request, 'list.html', {'works': works})

def work_detail(request, work_id, path):
    work = get_object_or_404(Works, id=work_id)
    
    # 获取当前 path 的所有行
    segments = Contents.objects.filter(work_id=work_id, path=path).order_by('global_order')
    
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
        'prev_path': prev_path,
        'next_path': next_path,
        'current_path': path
    })
