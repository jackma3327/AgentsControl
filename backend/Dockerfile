# syntax=docker/dockerfile:1
# 单阶段即可满足需求；构建期不联网装编译工具（依赖均有 wheel）。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖再拷代码，最大化层缓存命中
COPY pyproject.toml ./
RUN python -m pip install --upgrade pip && python -m pip install .

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# 首启运维脚本（create_user / audit_scopes）随镜像分发；
# 放在 app 之后：脚本变动不频繁，避免一次改动让 app 层缓存整体失效。
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 afc \
    && chown -R afc:afc /app
USER afc

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

# 生产由 Caddy 终止 TLS；容器内只跑 uvicorn 单进程，横向扩展交给编排
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
