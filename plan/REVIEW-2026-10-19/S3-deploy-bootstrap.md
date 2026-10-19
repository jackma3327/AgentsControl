# S3 · 部署、首启与种子数据

> 来自 `docs/PRODUCT-REVIEW-2026-10-19.md` Issue 3（P1）、Issue 10（P2，部署侧）、Issue 11（P2，seed 部分）。
> 只碰镜像、编排、Makefile 与文档，不写业务代码。**与其他 4 流的文件零交叉。**

> **状态：DONE（2026-10-19）**。D1 按「新增 W13」执行；`plan/windows.yaml` 仍冻结，W13 登记留给 Marshal 在波次边界完成。
> 验证：独立 compose 项目全量构建 → migrate 成功 → `make create-owner` → 登录 200；`make seed-demo` 两次行数不变；Caddy 下六个数据接口 200；Playwright 确认 API 请求全部同源 `/api/v1`；镜像 392MB（与改前持平）。

## 0. 任务身份

| 项 | 值 |
|---|---|
| 流 | `S3` |
| 名称 | 镜像可运维化、首启三步、同源注入、`make seed-demo` |
| 窗口 | **待定（决策 D1）**。建议新增 `W13 部署与首启` |
| 分支 | `win/s3-deploy-bootstrap` |
| 规模 | 4 个任务，约 1 人日 |
| 阻塞 | **启动前需 Marshal 授权 D1**；其余无依赖 |

### D1 是什么（必须先解决）

`backend/Dockerfile`、`deploy/` 根、`Makefile`、根 `README.md`、`.env.example` **不在 W01–W10 任何窗口的 `allow` 内**（`deploy/` 只有 `deploy/agent/` 归 W05）。本流的改动按现有边界会被 CI 拦下。

两个可选方案，取其一后再开工：

1. **新增 W13**（推荐）：`allow: [deploy/, backend/Dockerfile, backend/.dockerignore, Makefile, README.md, .env.example, backend/scripts/seed_demo.py]`，在 Wave 边界由 Marshal 登记。
2. **临时授权**：Marshal 在 PR 描述里批准本流的越界，CI 加白名单（临时方案，事后补 W13）。

## 1. 文件边界

**可改**

```
backend/Dockerfile
backend/.dockerignore                 # 不存在则新建
deploy/docker-compose.yml
deploy/docker-compose.dev.yml         # 仅在必须同步时
deploy/Caddyfile                      # 仅在反向代理需调整时（预期不需要）
Makefile
README.md
.env.example
backend/scripts/seed_demo.py          # 新增；W01 allow 内的新文件，W01 本流不碰 scripts/
```

**禁改**

```
backend/app/**            # S1 / S2
backend/migrations/**     # 不新增迁移
web/**                    # S1 / S4
scripts/**                # S5 的回归脚本，别动
.github/workflows/**      # W09
plan/windows.yaml         # 冻结，Marshal
```

## 2. 冻结契约

| 编号 | 契约 |
|---|---|
| C4 | 前端 API base 缺省 `""`，请求形如 `/api/v1/...`；本流 compose 注入 `NEXT_PUBLIC_API_BASE_URL=/api/v1`，与 S1-04 一致 |
| C6 | seed 脚本接口固定：`python scripts/seed_demo.py [--reset] [--owner owner@example.com]`，幂等，输出各表新增行数 |
| C7 | 首启命令三条固定：`docker compose ... up -d` → `alembic upgrade head`（migrate 服务已自动）→ `python scripts/create_user.py` |
| — | 不改变 `api` 容器 `read_only: true` 与 `no-new-privileges` 的安全设置 |

## 3. 任务

### S3-01 镜像纳入 `scripts/` 与 `migrations/`
- **现象**：`backend/Dockerfile` 只 COPY `app/`、`migrations/`、`alembic.ini`；容器内 `/app/scripts` 不存在，`create_user.py` 无法在容器里跑。
- **改动**
  - `COPY scripts ./scripts`（放在 `COPY app` 之前或之后均可，注意 layer 缓存：scripts 变动不频繁，放后面）。
  - 新建 `backend/.dockerignore`：排除 `.venv`、`__pycache__`、`.pytest_cache`、`tests/`、`*.pyc`、`.mypy_cache`——**不要**排除 `scripts/`、`migrations/`。
- **验收**
  ```bash
  docker compose -f deploy/docker-compose.yml build api
  docker compose -f deploy/docker-compose.yml run --rm --entrypoint sh api -c 'ls /app/scripts/create_user.py'
  docker compose -f deploy/docker-compose.yml run --rm --entrypoint python api scripts/create_user.py --help
  ```
  三条都成功；镜像体积增幅 < 5MB。

### S3-02 首启三步与 make 目标
- **现象**：全新环境 `users` 表 0 行，无注册端点，README 无 quickstart。
- **改动**
  - `README.md` 增加「首次启动」段：复制 `.env`、`make bootstrap`、`make create-owner EMAIL=... PASSWORD=...`、打开 `:8080` 登录。每条命令给可复制的完整形式（含 `--env-file .env`）。
  - `Makefile` 增加：
    - `bootstrap`：`docker compose -f deploy/docker-compose.yml --env-file .env up -d --build`（migrate 由 compose 的 migrate 服务完成）
    - `create-owner`：`docker compose ... exec api python scripts/create_user.py --subject "$(EMAIL)" --password "$(PASSWORD)"`（参数名以脚本实际参数为准，先读 `create_user.py`）
    - `logs` / `ps` 顺手补齐（可选）
  - `.env.example` 增加 `LITELLM_BASE_URL=` 段并注明「不填则三问不可用，其余功能正常」——这是评审 Issue 4 的排障前提。
- **验收**
  - 清空 volume（`docker compose down -v`）→ 按 README 三条命令 → 10 分钟内能登录。
  - `make help` 能看到新目标；`make create-owner` 参数缺失时报错清晰（`EMAIL is required`）。
- **不做**：`/setup` 首启引导页（决策 D3，涉及"首启开放写 owner 密码"的暴露面）。

### S3-03 compose 注入同源 API 地址
- **现象**：`web` 服务只声明了 build args 且为 `${NEXT_PUBLIC_API_BASE_URL:?...}`，实际 `.env` 未填，前端回退硬编码 `localhost:8000`。
- **改动**
  - `deploy/docker-compose.yml#web.build.args`：`NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:-/api/v1}`。
  - `.env.example`：`NEXT_PUBLIC_API_BASE_URL=/api/v1`（注释：同源相对路径，由 Caddy 反代 `/api/*`；不要把 `:8000` 写进来）。
  - 若 Caddy 的 `/api/*` 前缀与后端 `API_PREFIX=/api/v1` 有双前缀问题，先读 `deploy/Caddyfile` 再定值，**不要靠猜**。
- **验收**：`docker compose up` 后浏览器 Network 里请求全部是 `http://<host>:8080/api/v1/...`；`docker compose exec web env | grep NEXT_PUBLIC` 为空也无所谓（这是 build arg，不是 runtime env），但构建日志里能看到传入值。
- **注意**：`NEXT_PUBLIC_*` 是**构建期**注入，改完必须 `--build` 重建 web，否则改了 compose 也不生效——README 里要写这句。

### S3-04 `make seed-demo`
- **改动**
  - 新建 `backend/scripts/seed_demo.py`：直接复用 app 的 SQLAlchemy 模型与 `get_sessionmaker()`，造 2 goals / 4 intake / 5 tasks / 4 approvals / 10 ledger / 4 devices；`--reset` 先按固定标记（如 `title LIKE '[demo]%'` 或 `external_id` 前缀）清掉旧演示数据；幂等，重复执行不翻倍。
  - `Makefile`：`seed-demo: docker compose ... exec api python scripts/seed_demo.py --reset`。
  - README 补一行：`make seed-demo`（空库时用来快速看到界面）。
- **验收**
  - 空库 `make seed-demo` → 六个页面都有数据；再执行一次，行数不变（幂等）。
  - 脚本输出：`goals=2 intake=4 tasks=5 approvals=4 ledger=10 devices=4`。
  - 与 S4 的「示例数据标注」共用同一批标记（约定：`[demo]` 前缀），S4 读这个标记渲染标签。
- **依赖**：需 S2-01 已合入，否则 intake/goals 写不进去（运行期依赖，文件不冲突；S2 未合时先用 psql 验证其它表）。

## 4. 自检

```bash
docker compose -f deploy/docker-compose.yml config >/dev/null   # 语法
docker compose -f deploy/docker-compose.yml build               # 构建
docker compose -f deploy/docker-compose.yml up -d
make seed-demo
curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/        # 200
./scripts/pr_precheck.sh --local
```

## 5. 完成定义

- [x] 容器内能跑 `create_user.py`
- [x] 清库后按 README 10 分钟内登录成功
- [x] 浏览器请求全同源
- [x] `make seed-demo` 幂等
- [x] `.env.example` 写明 `LITELLM_BASE_URL` 可选语义

## 6. PR 与提交

单 PR：`[W13] chore: deployable image and first-run bootstrap`（窗口号按 D1 结论替换）。
提交前缀：`fix(#<issue>): bootstrap` / `chore(#<issue>): seed-demo`。

**PR 描述必须包含**：清库复现步骤 + 三条命令的实际输出 + 镜像体积前后对比。

## 7. 卡住时

- D1 未批 → 只写 `backend/scripts/seed_demo.py`（新文件、不触发边界检查），其余改动挂 Draft。
- 发现 `create_user.py` 参数与 Makefile 假设不符 → 以脚本为准改 Makefile，不改脚本（脚本归 W01）。
- Caddy 前缀不确定 → 读 `deploy/Caddyfile`，仍不确定就在 PR 里 @ W01，不要猜一个值上线。
