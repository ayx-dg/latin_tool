"""
URL configuration for MyDjango project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from index import views
from index import models

urlpatterns = [
    path('admin/', admin.site.urls),
    #path('', index)
    path('', views.work_list, name='work_list'),
    path('work/<int:work_id>/<path:path>/', views.work_detail, name='work_detail'),
    # 新增：只输入 ID 时的处理
    path('work/<int:work_id>/', views.work_redirect, name='work_redirect'),
    path('api/gloss', views.api_get_gloss, name='api_get_gloss')
]
