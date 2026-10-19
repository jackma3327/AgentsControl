.PHONY: help setup dev-api dev-web test lint migrate agent-build down up-deps bootstrap create-owner seed-demo logs ps

# 单机编排：所有部署目标共用同一份 compose 与同一个 .env
COMPOSE ?= docker compose -f deploy/docker-compose.yml --env-file .env

help:
	@echo "make setup     安装后端与前端依赖"
	@echo "make dev-api   本地起 API（:8000）"
	@echo "make dev-web   本地起 Web（:3000）"
	@echo "make test      后端单测 + agent 构建"
	@echo "make lint      ruff + gofmt"
	@echo "make migrate   升级数据库到最新（本机进程）"
	@echo "make up-deps   只起本地 PG + Redis"
	@echo "make down      停掉本地 compose 依赖"
	@echo ""
	@echo "首次启动（Docker）："
	@echo "make bootstrap                    构建并拉起 db/redis/migrate/api/web/caddy"
	@echo "make create-owner EMAIL=... PASSWORD=...  创建/重置登录用户"
	@echo "make seed-demo                    灌入 [demo] 演示数据（幂等）"
	@echo "make logs / make ps               看日志 / 看容器状态"

setup:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
	cd web && npm install

dev-api:
	cd backend && uvicorn app.main:app --reload --port 8000

dev-web:
	cd web && npm run dev

test:
	cd backend && pytest -q
	cd agent && go vet ./... && go build ./...

lint:
	cd backend && ruff check app tests
	cd agent && test -z "$$(gofmt -l .)"

migrate:
	cd backend && alembic upgrade head

agent-build:
	cd agent && go build -o bin/afc-agent ./cmd/agent

# 本地只起 PG + Redis（生产用 deploy/docker-compose.yml）
up-deps:
	docker compose -f deploy/docker-compose.dev.yml up -d

down:
	docker compose -f deploy/docker-compose.dev.yml down

# ---------------------------------------------------------------------------
# 首启与演示数据（deploy/docker-compose.yml）
# ---------------------------------------------------------------------------

bootstrap:
	@test -f .env || { echo "缺少 .env：先执行 cp .env.example .env，再按需修改域名/密码" >&2; exit 1; }
	$(COMPOSE) up -d --build
	@echo ""
	@echo "启动完成。打开 http://localhost:8080 登录（首次需先 make create-owner）。"

create-owner:
	@test -n "$(EMAIL)" || { echo "EMAIL is required：make create-owner EMAIL=owner@example.com PASSWORD='至少8位'" >&2; exit 1; }
	@if [ -n "$(PASSWORD)" ]; then \
		$(COMPOSE) exec -T api python scripts/create_user.py --subject "$(EMAIL)" --password "$(PASSWORD)"; \
	else \
		echo "PASSWORD 未提供，改为交互输入（需要 TTY）："; \
		$(COMPOSE) exec api python scripts/create_user.py --subject "$(EMAIL)"; \
	fi

seed-demo:
	$(COMPOSE) exec -T api python scripts/seed_demo.py --reset

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps
