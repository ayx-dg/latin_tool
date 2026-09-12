FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.4 /uv /usr/local/bin/uv

WORKDIR /app

# 先只拷依赖文件，利用 Docker 缓存层
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . .

# 静态文件打包进镜像，由 whitenoise 直接提供
RUN DJANGO_DEBUG=false python manage.py collectstatic --noinput --clear

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn MyDjango.wsgi:application --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 180 --access-logfile - --error-logfile -"]
