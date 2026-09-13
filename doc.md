 Latin Library — 拉丁文库阅读与标注工具

在线阅读拉丁文原著，点击单词查看中文释义和语法分析。后端调用 Gemini API 自动生成标注，结果缓存至数据库。

## 项目结构

```
├── MyDjango/              # Django 项目配置
│   ├── settings.py        # 配置：DB、Redis、API Key、APP
│   ├── urls.py            # 根路由
│   ├── wsgi.py / asgi.py  # 部署入口
│   └── admin.py           # 管理后台注册
├── index/                 # 主应用
│   ├── views.py           # 页面视图 + API + Gloss 业务逻辑
│   ├── models.py          # Works / Contents / GlossCache
│   ├── admin.py           # 后台注册
│   ├── migrations/        # 数据库迁移
│   └── static/gloss.js    # 前端标注渲染脚本
├── templates/
│   ├── list.html          # 作品列表页
│   └── detail.html        # 阅读 + 标注页
├── latin_library.db       # SQLite 数据库
├── manage.py              # Django 管理脚本
├── .env                   # API 密钥（不入库）
├── pyproject.toml         # 依赖管理
└── doc.md                 # 本文件
```

## 快速开始

```bash
# 1. 创建虚拟环境
uv venv
source .venv/bin/activate

# 2. 安装依赖
uv sync

# 3. 配置 .env
cp .env.example .env   # 填入 GEMINI_API_KEY 和 TURNSTILE_SECRET

# 4. 运行
uv run manage.py runserver
```

## 路由

| 路径 | 视图 | 说明 |
|------|------|------|
| `/` | `work_list` | 作品列表 |
| `/work/<id>/` | `work_redirect` | 跳转到作品第一页 |
| `/work/<id>/<path>/` | `work_detail` | 阅读 + 标注页 |
| `/api/gloss` | `api_get_gloss` | API：获取标注（需 Turnstile token） |
| `/admin/` | Django Admin | 管理后台 |

## 数据模型

### Works（作品）
| 字段 | 类型 | 说明 |
|------|------|------|
| author | Text | 作者 |
| title | Text | 标题 |
| urn | Text | URN 标识 |

### Contents（正文片段）
| 字段 | 类型 | 说明 |
|------|------|------|
| work_id | Integer | 外键 → Works |
| text | Text | 拉丁文原文 |
| path | Text | 路径标识，用于翻页 |
| global_order | Integer | 全局排序 |
| subtype / div_type / n_value | Text | 结构信息 |

### GlossCache（标注缓存）
| 字段 | 类型 | 说明 |
|------|------|------|
| text_hash | Char(64) | MD5 哈希（唯一索引） |
| gloss_data | JSON | Gemini 返回的标注数据 |
| created_at | DateTime | 创建时间 |

> `Works` 和 `Contents` 表为 `managed = False`，由外部工具管理（`inspectdb` 自动生成）。

## 标注流程

```
用户点击"加载原文分析"
    → Turnstile 验证
    → GET /api/gloss?work_id=&path=&cf_token=
    → 查询 Contents 拼接原文
    → 检查 GlossCache（MD5）
        ├─ 命中 → 直接返回
        └─ 未命中 → 调用 Gemini API → 存入缓存 → 返回
    → 前端 TreeWalker 替换文本节点为交互式 span
    → 点击单词弹出释义（m）+ 语法（g）
```

### Gemini API

模型：`gemini-2.5-flash`

Prompt 将拉丁文分词编号，要求返回 JSON 数组：
```json
[
  {"m": "写", "g": "动词，不定式"},
  {"m": "关于", "g": "介词"},
  ...
]
```

### 缓存策略

- **DB 缓存**：`GlossCache` 表，以 MD5 为 key，永久存储
- **Redis 缓存**：配置了 `django-redis`，目前用于可选限流

## 限流

`check_rate_limit(key, limit, period)` 基于 Redis 实现计数器限流。当前未接入标注流程，视需要启用。

## 单元测试

```bash
uv run python -m pytest index/tests.py -v
```

测试文件位于 `index/tests.py`，涵盖 5 个测试类（共 10 个测试用例）：

| 测试类 | 测试内容 |
|--------|----------|
| `TestExtractWords` | 拉丁文分词：提取 Unicode 字母单词；处理空/空白输入 |
| `TestBuildGlossPrompt` | Prompt 构建：包含索引编号；要求返回 JSON 数组 |
| `TestFallbackGloss` | 回退标注：为每个单词生成"解析失败"；纯数字/符号的分词 |
| `TestGetOrCreateGloss` | 缓存逻辑：空文本返回空；已缓存的标注直接从数据库返回 |
| `TestCheckRateLimit` | 限流器：首次请求通过并设置缓存；超出限制返回 False |

依赖：需要安装 `pytest` 和 `pytest-django`（已加入 `pyproject.toml` 的 dev 依赖）。

## 配置

| 变量 | 来源 | 用途 |
|------|------|------|
| `GEMINI_API_KEY` | `.env` | Google Gemini API |
| `TURNSTILE_SECRET` | `.env` | Cloudflare Turnstile 验证 |
| Redis | `settings.py` | 缓存/限流后端 |
| SQLite | `latin_library.db` | 主数据库 |

## 标注模型与额度

### 默认模型

`gemini-3.5-flash-lite`（`index/llm.py:DEFAULT_GEMINI_MODEL`）。

Gemini 2.x 已全面停用（`gemini-2.5-flash` 仍在但免费额度仅约 **20 次/天**，
`2.5-flash-lite` / `2.0-flash` 直接返回 404）。当前可用：

| 模型 | 实测 | 说明 |
|---|---|---|
| `gemini-3.5-flash-lite` | 约 2.5s / 短章 | **默认**，免费额度宽松 |
| `gemini-3.5-flash` | 约 8-15s | 语法分析更细致，慢 3-4 倍 |
| `gemini-3.8-flash` | 429 | 免费额度已不可用 |

换模型：`LLM_MODEL=gemini-3.5-flash`（或 `LLM_PROVIDER=openai_compatible` + `LLM_BASE_URL`/`LLM_API_KEY`）。

### 额度现实与应对

免费额度按**请求次数**计，分块后一章要 1-3 次请求，所以：

- **别指望全量标注**：288041 行正文不可能靠免费额度跑完
- **策略：按需标注 + 永久缓存 + 离线预热**。用户点过的章节永久命中缓存（约 1 秒返回）
- `GLOSS_CHUNK_WORDS`（默认 150）调大可减少请求数，但过大会被判 504（459 词整章一次必超时）
- 每天跑一次 `prefetch_gloss.py` 慢慢积累，遇 429 脚本会自动停下

```bash
# 1. 探针：模型通不通、返回能不能和词表对齐（不查缓存）
uv run python scripts/check_gloss.py
uv run python scripts/check_gloss.py --repeat 5        # 观察限流
uv run python scripts/check_gloss.py --work-id 1 --path <path>   # 用真实章节

# 2. 离线预热：提前把标注算好写进 GlossCache
uv run python scripts/prefetch_gloss.py --dry-run --limit 20     # 先看计划
uv run python scripts/prefetch_gloss.py --limit 10 --sleep 4     # 真跑，每章间隔 4 秒
```

相关环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GLOSS_TIMEOUT` | 60 | 单次模型请求超时（秒） |
| `GLOSS_RETRIES` | 2 | 失败重试次数，指数退避 |
| `TURNSTILE_ENABLED` | true | 关掉可去掉 Cloudflare 验证，体验更顺 |

健壮性约定：**模型返回的数量/索引和词表对不上时不写 `GlossCache`**，避免错位结果被永久缓存；
前端按 `\p{L}+` 顺序消费数组，一旦错位整页标注都会平移。

## 测试

```bash
uv run pytest                # 22 个单元测试 + 2 个跳过（约 1 秒）
RUN_LIVE_GLOSS=1 uv run pytest index/test_gloss_live.py -v -s   # 真实调模型
```

测试统一走 `MyDjango/settings_test.py`（内存 SQLite），不会碰 Supabase；
要针对真实库跑：`uv run pytest --ds=MyDjango.settings`。

## 部署检查

```bash
uv run manage.py check --deploy
```

开发环境 `DEBUG = True`，生产需关闭并配置：
- `SECRET_KEY` 更换为随机长字符串
- `ALLOWED_HOSTS` 限定域名
- `SECURE_SSL_REDIRECT = True`
- `CSRF_COOKIE_SECURE = True`
- `SESSION_COOKIE_SECURE = True`

以上已由环境变量接管，见「部署」。

## 部署（Render + Supabase）

架构：Render 免费 Web Service 跑 Docker 容器，数据库用 Supabase 免费 Postgres。

### 1. Supabase

1. 新建项目，地区建议和 Render 一致（如 Singapore）
2. `Project Settings → Database → Connection string → URI`，复制（端口 6543 的 pooler 地址）
3. 密码里有特殊字符时先 URL encode

> ⚠️ **两个必踩的坑**
> - **必须用 pooler，不能用直连**：`db.<ref>.supabase.co` 现在只有 IPv6 记录（无 A 记录），
>   Render 的 IPv4 出口连不上（直连 IPv4 是付费 add-on）。用
>   `aws-0-<region>.pooler.supabase.com`。
> - **pooler 的用户名是 `postgres.<ref>`，不是 `postgres`**：写成 `postgres` 会
>   `FATAL: password authentication failed for user "postgres"`。

### 2. 导入数据

```bash
export DATABASE_URL='postgresql://postgres.<ref>:<password>@<host>:6543/postgres'
uv run python manage.py migrate            # 建 gloss_cache / auth 等表
uv run python scripts/sqlite_to_postgres.py latin_library.db
```

`works` / `contents` 是 `managed = False`，`migrate` 不建表，由脚本建表并批量导入（28.8 万行，几分钟）。
脚本用 `ON CONFLICT DO NOTHING`，可重复执行。

### 3. Render

1. New → Blueprint，连 `latin_tool` 仓库，读 `render.yaml`
2. 环境变量 `DATABASE_URL` / `GEMINI_API_KEY` / `TURNSTILE_SECRET` 手动填（`sync: false` 的项）
3. `DJANGO_SECRET_KEY` 由 `generateValue` 自动生成

构建流程：Dockerfile 用 `uv export` 安装依赖 → `collectstatic` → 启动时 `migrate` + gunicorn。

### 注意事项

- Render 免费版 15 分钟无请求会休眠，冷启动约 30–60 秒
- Supabase 免费项目 7 天无访问会暂停，需在控制台恢复
- 静态文件由 whitenoise 提供，不需要单独的对象存储
- 未配置 `REDIS_URL` 时缓存退化为本地内存，限流功能失效但不影响阅读
- `latin_library.db`（62MB）已加入 `.dockerignore`，不进镜像；可考虑从 git 历史中移除
