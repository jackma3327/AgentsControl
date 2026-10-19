"""w08_01_evals: 资产面（题库 / 跑批 / 结果 / skill / harness / 认知卡 / 漂移）

Revision ID: w08_01_evals
Revises: w08_00_merge_heads
Create Date: 2026-10-19

窗口: W08（issue W08-I01 / I02 / I04 / I05 / I06）

设计取舍：
- 时间统一 timestamptz，金额/成本用 numeric（浮点会在对账时出现分位误差）；
- `eval_results` 唯一约束 (run_id, task_id, model)：重跑生成新 run，绝不在旧 run 上覆盖；
- `drift_alerts` 唯一约束 (model, dimension, week)：一周只报一次，重复扫描幂等；
- 题库 20 题随迁移灌入（8 维各 ≥2），题面/评分锚点都写死在迁移里，便于回溯"当时测了什么"。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w08_01_evals"
down_revision: str | None = "w08_00_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dimension", sa.String(32), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("rubric", sa.Text(), nullable=False),
        sa.Column("weight", sa.Numeric(6, 3), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("week", sa.String(16), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued", index=True),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("cost_cap_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("cost_used_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False, index=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("week", sa.String(16), nullable=False),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_ref", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("run_id", "task_id", "model", name="uq_eval_result"),
    )
    op.create_index(
        "ix_eval_results_week_model", "eval_results", ["week", "model", "dimension"]
    )

    op.create_table(
        "skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, index=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft", index=True),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("trigger", sa.Text(), nullable=False, server_default=""),
        sa.Column("criteria", sa.Text(), nullable=False, server_default=""),
        sa.Column("counterexample", sa.Text(), nullable=False, server_default=""),
        sa.Column("eval_task_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("rollout_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rollout_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("name", "version", name="uq_skill_version"),
    )

    op.create_table(
        "harness_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, index=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("skill_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("eval_task_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("name", "version", name="uq_harness_version"),
    )

    op.create_table(
        "cognition_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("counterexample", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reuse_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft", index=True),
        sa.Column("last_reused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "drift_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model", sa.String(64), nullable=False, index=True),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("week", sa.String(16), nullable=False),
        sa.Column("prev_week", sa.String(16), nullable=False),
        sa.Column("prev_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("curr_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("delta_pct", sa.Numeric(6, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open", index=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("model", "dimension", "week", name="uq_drift_week"),
    )

    _seed_tasks()


def _seed_tasks() -> None:
    """20 道自有题：8 维各 ≥2。题面写真实任务，评分锚点写"什么算 100 分"。"""
    rows = [
        # --- writing 长文写作（3）---
        ("evt-w1", "writing", "技术方案的长文结构", "写一份 2000 字的内部技术方案：为什么放弃微服务、保留单体，必须包含取舍与失败预案。", "25 结构可执行 / 25 取舍有代价 / 25 失败预案具体 / 25 无空话"),
        ("evt-w2", "writing", "把事故复盘写成可复用规则", "根据一次迁移分叉事故，写出复盘并提炼 3 条可执行规则。", "30 时间线准确 / 30 根因非表层 / 40 规则可被他人直接执行"),
        ("evt-w3", "writing", "面向非技术读者的解释", "用 800 字向非技术客户解释「为什么小程序必须备案域名」，不得使用术语。", "40 无术语 / 30 类比准确 / 30 给出行动建议"),
        # --- code 代码（3）---
        ("evt-c1", "code", "并发安全的租约回收", "用 Python + Postgres 实现租约回收：要求单条 SQL、幂等、可并发。给出代码与测试。", "40 无竞态 / 30 幂等 / 30 测试能证伪"),
        ("evt-c2", "code", "给现有函数补边界测试", "为分页函数补 6 个边界用例（空页、越界 cursor、并发插入、重复键、超长文本、时钟回拨）。", "60 用例真能失败 / 40 覆盖并发与时钟"),
        ("evt-c3", "code", "最小改动修 bug", "修复一个时区导致的「今天」判断错误，要求改动 ≤10 行且附回归测试。", "50 改动最小 / 50 回归测试可复现"),
        # --- extraction 结构化抽取（2）---
        ("evt-e1", "extraction", "从账单邮件抽取订阅", "从 3 封账单邮件抽取：商户、金额、周期、下次扣费日、可否降级。输出 JSON。", "50 字段完整 / 30 金额周期准确 / 20 不确定项标注而非编造"),
        ("evt-e2", "extraction", "从截图提取工具信息", "从一张工具介绍截图提取：名称、仓库地址、许可证、是否活跃。缺失就写 null。", "50 不编造 / 30 许可证识别 / 20 null 语义正确"),
        # --- tool_use 多轮工具调用（3）---
        ("evt-t1", "tool_use", "多步检索后给出结论", "用搜索 + 读页两步，回答「某开源项目最近一次破坏性变更是什么」，必须给出来源链接。", "40 步骤正确 / 40 结论有出处 / 20 承认不确定"),
        ("evt-t2", "tool_use", "工具失败后的降级", "第一次调用返回 500 时，改用备用接口完成任务，并说明降级原因。", "50 正确降级 / 30 不丢任务 / 20 记录原因"),
        ("evt-t3", "tool_use", "写操作前先确认", "在删除数据前必须先读取并回显待删对象，得到确认后再执行。", "60 有确认步骤 / 40 不可跳过"),
        # --- vision 截图与视觉理解（2）---
        ("evt-v1", "vision", "读仪表盘截图取数", "从一张监控截图中读出：QPS、错误率、P99 延迟，并指出异常项。", "60 三个数值准确 / 40 异常判断有依据"),
        ("evt-v2", "vision", "识别 UI 的可访问性问题", "看一张页面截图，指出至少 3 个可访问性问题（对比度/焦点/标签）。", "60 问题真实 / 40 给出修法"),
        # --- long_context 长上下文（2）---
        ("evt-l1", "long_context", "跨文档找矛盾", "给定 5 份文档（约 3 万字），找出两处互相矛盾的配置项并给出页码。", "60 找全 / 40 定位准确"),
        ("evt-l2", "long_context", "长对话后仍守住约束", "在 20 轮对话后仍遵守最初的三条硬约束（不改配置、不删数据、不改接口）。", "70 约束不丢 / 30 能说明为何拒绝"),
        # --- chinese 中文语感（2）---
        ("evt-z1", "chinese", "把翻译腔改成人话", "把一段 300 字的翻译腔产品文案改成中文母语者会说的话，保留信息量。", "50 无翻译腔 / 30 信息不丢 / 20 语气一致"),
        ("evt-z2", "chinese", "识别并修正歧义句", "找出 3 个歧义句并改写，说明原句可能的两种理解。", "60 歧义识别正确 / 40 改写无新歧义"),
        # --- instruction 指令遵循（3）---
        ("evt-i1", "instruction", "严格按格式输出", "按给定 JSON Schema 输出，禁止多余字段与解释性文字。", "70 字段与类型完全正确 / 30 无多余输出"),
        ("evt-i2", "instruction", "否定约束", "完成改写任务，但不得使用「赋能」「闭环」「抓手」三个词。", "60 约束零违反 / 40 改写质量"),
        ("evt-i3", "instruction", "冲突指令的取舍", "当「尽量详细」与「不超过 200 字」冲突时，优先字数限制并说明。", "60 优先正确 / 40 说明简洁"),
    ]
    tasks = sa.table(
        "eval_tasks",
        sa.column("id", sa.String),
        sa.column("dimension", sa.String),
        sa.column("title", sa.String),
        sa.column("prompt", sa.Text),
        sa.column("rubric", sa.Text),
        sa.column("weight", sa.Numeric),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(
        tasks,
        [
            {
                "id": tid,
                "dimension": dim,
                "title": title,
                "prompt": prompt,
                "rubric": rubric,
                "weight": 1,
                "active": True,
            }
            for tid, dim, title, prompt, rubric in rows
        ],
    )


def downgrade() -> None:
    op.drop_table("drift_alerts")
    op.drop_table("cognition_cards")
    op.drop_table("harness_templates")
    op.drop_table("skills")
    op.drop_index("ix_eval_results_week_model", table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("eval_tasks")
