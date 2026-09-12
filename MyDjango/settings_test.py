"""测试用 settings：强制走内存 SQLite，避免把 test_ 库建到 Supabase 上。

pytest.ini_options 里 DJANGO_SETTINGS_MODULE 指向本模块。
需要针对真实 Postgres 跑时用: uv run pytest --ds=MyDjango.settings
"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# 测试环境不要真的发请求去验证人机
TURNSTILE_ENABLED = False
