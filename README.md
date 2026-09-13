# Latin Library — 拉丁语阅读器

Django 6 + Supabase Postgres 部署的拉丁语在线阅读器，支持逐词标注（公开词典）和整章中文讲解（AI）。

在线地址：**https://latin-library.onrender.com**

## 功能

- **逐词标注**：点击任意单词查看形态分析 + 释义，数据来源为英文维基词典拉丁语词条，首次查询后永久缓存
- **整章讲解**：AI 生成中文讲解（语法难点 + 大意概括 + 翻译），结果永久缓存
- **模型回退链**：Token Plan 优先，失败自动回退 Gemini
- **Cloudflare Turnstile**：可选的人机验证，部分网络环境下默认关闭

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Django 6 + Python 3.12 |
| 数据库 | Supabase Postgres（免费 tier，pooler 连接） |
| 前端 | 原生 JS，无框架依赖 |
| 容器 | Docker（python:3.12-slim + uv） |
| 部署 | Render Free Plan（Docker） |
| AI 模型 | Google Gemini / 腾讯云 Token Plan（OpenAI 兼容） |

## 本地开发

### 1. 环境准备

```bash
# 安装 uv（如果还没装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆
git clone https://github.com/ayx-dg/latin_tool.git
cd latin_tool

# 安装依赖
uv sync

# 创建 .env
cp .env.example .env
# 编辑 .env 填入必要的 key
```

### 2. 数据库

本地开发可以使用 SQLite，无需安装 Postgres：

```bash
# 填 .env（如果用 Supabase）
# DATABASE_URL=postgresql://postgres.<ref>:<pw>@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres

# 迁移
uv run python manage.py migrate

# 导入数据（如果有 latin_library.db）
uv run python scripts/sqlite_to_postgres.py --db latin_library.db
```

### 3. 启动

```bash
uv run python manage.py runserver 0.0.0.0:8000
```

打开 http://localhost:8000

## 测试

```bash
uv run python -m pytest -q          # 42 tests, ~1s
uv run python -m pytest -q -x       # 遇到第一个失败就停
```

测试使用内存 SQLite（`MyDjango/settings_test.py`），不连 Supabase 生产库。

## Docker

```bash
# 构建
docker build -t latin-library .

# 运行（传入 .env）
docker run --env-file .env -p 8000:8000 latin-library

# 访问 http://localhost:8000
```

镜像约 200MB，包含静态文件（whitenoise 直接提供，不需要 nginx）。

## 部署到 Render

### 方式一：Blueprint（推荐）

1. Fork 仓库到自己的 GitHub
2. 在 Render 控制台点 **New > Blueprint**，选择 fork 出来的仓库
3. Render 会自动读取 `render.yaml` 创建服务
4. 填入环境变量（见下方），点 **Deploy**

### 方式二：手动创建 Docker 服务

1. 在 Render 创建 **Web Service**，选 **Docker** 运行时
2. 连接你的仓库
3. 配置：
   - **Dockerfile path**: `./Dockerfile`
   - **Health check path**: `/healthz`
   - **Plan**: Free
   - **Region**: Singapore（推荐，和 Supabase 同区）
4. 填入环境变量后 Deploy

### 必需的环境变量

| 变量 | 说明 | 必填 |
|---|---|---|
| `DATABASE_URL` | Supabase Postgres 连接串（pooler 端口 6543） | 是 |
| `DJANGO_SECRET_KEY` | 生成随机字符串 | 是 |
| `DJANGO_DEBUG` | 设为 `false` | 是 |
| `ALLOWED_HOSTS` | 设为 `*`（Render 健康检查用内部主机名） | 是 |
| `GEMINI_API_KEY` | Google AI Studio key，AI 讲解用 | 是（二选一） |
| `LLM_API_KEY` | 腾讯云 Token Plan key，优先级高于 Gemini | 二选一 |
| `LLM_BASE_URL` | Token Plan 地址：`https://api.lkeap.cloud.tencent.com/plan/v3` | 用 LLM_API_KEY 时必填 |
| `LLM_MODEL` | Token Plan 模型：`deepseek-v4-flash-202605` | 用 LLM_API_KEY 时必填 |

### 可选环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `TURNSTILE_ENABLED` | Cloudflare Turnstile 验证 | `false` |
| `TURNSTILE_SECRET` | Turnstile secret key | — |
| `TURNSTILE_HOSTNAMES` | 允许的 hostname，逗号分隔 | — |
| `LLM_PROVIDERS` | 模型回退顺序，逗号分隔 | 自动检测 |
| `GLOSS_AI_WORDS` | 词典查不到时用 AI 补词 | `false` |
| `REDIS_URL` | Redis 连接串（无则用 Django locmem） | — |

## 数据库

### Supabase Postgres

免费 tier 足够使用。推荐配置：

1. 创建项目，选 **Singapore**（ap-southeast-1）
2. 在 **Settings > Database** 找到 **Connection string > URI**
3. 使用 **Transaction mode (6543)** 端口
4. 注意：直接 host（`db.xxx.supabase.co`）是 IPv6 only，大部分环境需要走 pooler

```
postgresql://postgres.<project-ref>:<password>@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
```

### 表结构

| 表 | 说明 |
|---|---|
| `works` | 书目信息（id, author, title） |
| `contents` | 章节原文（work_id, path, text, global_order） |
| `gloss_cache` | 逐词标注缓存（work_id, path → gloss JSON） |
| `dict_entry` | 词典条目缓存（word_form → 形态 + 释义） |
| `chapter_note` | AI 讲解缓存（work_id, path → 中文讲解） |

### 导入数据

```bash
# 从 SQLite 迁移到 Supabase Postgres
uv run python scripts/sqlite_to_postgres.py \
  --db latin_library.db \
  --database-url "postgresql://postgres.xxx:pw@host:6543/postgres"

# 逐表导入（指定表名）
uv run python scripts/sqlite_to_postgres.py \
  --db latin_library.db \
  --database-url "..." \
  --tables works contents gloss_cache
```

### 预热词典

```bash
# 扫描全库，预热出现频率最高的 300 个词（零 AI 调用）
uv run python scripts/build_dict.py --top 300

# 扫描特定章节
uv run python scripts/build_dict.py --work-id 2 --limit 20
```

### 可选：补充中文释义

```bash
# 用 AI 批量给词典条目补中文（离线跑，不在请求路径里，默认不启用）
uv run python scripts/fill_dict_zh.py --limit 200
```

## API

| 路径 | 说明 |
|---|---|
| `GET /healthz` | 健康检查（不查数据库） |
| `GET /api/gloss?work_id=1&path=...` | 逐词标注（返回词数、释义、缺失数） |
| `GET /api/explain?work_id=1&path=...` | 整章讲解（返回中文讲解 + provider 信息） |

## 项目结构

```
.
├── Dockerfile
├── render.yaml              # Render 部署配置
├── pyproject.toml           # 依赖 + pytest 配置
├── manage.py
├── MyDjango/
│   ├── settings.py          # 主配置（env 驱动）
│   ├── settings_test.py     # 测试配置（内存 SQLite）
│   └── urls.py              # 路由
├── index/
│   ├── models.py            # DictEntry, ChapterNote, GlossCache
│   ├── views.py             # 渲染 + API（gloss, explain）
│   ├── llm.py               # 模型 provider 抽象层
│   ├── dicts.py             # 公开词典（Wiktionary 拉丁语）
│   ├── tests.py             # 42 个测试
│   └── test_gloss_live.py   # 在线模型测试（需 RUN_LIVE_GLOSS=1）
├── templates/
│   └── detail.html          # 阅读页面（含前端 JS）
├── scripts/
│   ├── build_dict.py        # 词典预热
│   ├── fill_dict_zh.py      # 中文释义补充（可选）
│   ├── check_gloss.py       # 模型探针
│   ├── prefetch_gloss.py    # 批量缓存预热
│   ├── sqlite_to_postgres.py # SQLite → Postgres 迁移
│   └── notify.sh            # ntfy + 桌面通知
└── doc.md                   # 详细部署文档
```

## License

MIT
