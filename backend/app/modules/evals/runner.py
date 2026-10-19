"""evals runner：题库执行与成本控制。

设计要点：
1. **不在请求里等模型**：`POST /evals/runs` 只建 run（queued），执行交给 runner。
2. **成本上限是硬约束**：逐题累加，超过 `cost_cap_usd` 立即中止（status=aborted），
   保留已完成结果——半份数据比"全丢"有用。
3. **7 天缓存**：同 (task_id, model) 且 7 天内跑过就直接复用分数、成本记 0，
   避免每周重复烧同一笔钱。
4. 生产 provider 走 LiteLLM（本窗口只落接口与 fake）；测试用 fake，不出网。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .models import EvalResult, EvalRun, EvalTask
from .repository import EvalRepository, new_id

CACHE_TTL = timedelta(days=7)
DEFAULT_MODELS: tuple[str, ...] = (
    "claude-opus-4.5",
    "gpt-5.1",
    "gemini-3-pro",
    "deepseek-v4.1",
    "qwen4-max",
)


@dataclass(frozen=True, slots=True)
class ModelScore:
    score: float
    cost_usd: float
    latency_ms: int
    raw_ref: str | None = None


class EvalProvider(Protocol):
    """模型调用抽象。真实实现走 LiteLLM；测试注入 FakeProvider。"""

    async def score(self, task: EvalTask, model: str) -> ModelScore: ...


class FakeProvider:
    """确定性假 provider：同 (task, model) 永远同分，便于断言与复现。"""

    def __init__(self, base: float = 62.0) -> None:
        self._base = base

    async def score(self, task: EvalTask, model: str) -> ModelScore:
        digest = hashlib.sha256(f"{task.id}:{model}".encode()).digest()
        # 40 + [0,55] → 40–95，避免出现"全 100"这种不可信数据
        score = 40.0 + (digest[0] / 255) * 55.0
        # 强模型名给一点系统性优势，让雷达图有区分度
        if any(k in model for k in ("opus", "gpt-5", "gemini-3")):
            score = min(98.0, score + 6)
        latency = 300 + int.from_bytes(digest[1:3], "big") % 2200
        cost = round(0.002 + (digest[3] / 255) * 0.018, 6)
        return ModelScore(
            score=round(score, 2),
            cost_usd=cost,
            latency_ms=latency,
            raw_ref=f"fake://{task.id}/{model}",
        )


@dataclass(slots=True)
class RunOutcome:
    run_id: str
    status: str
    executed: int
    cached: int
    cost_used_usd: float
    aborted_reason: str | None = None


def _cache_key(task_id: str, model: str) -> str:
    return f"{task_id}|{model}"


async def build_cache(
    repo: EvalRepository, *, now: datetime | None = None
) -> dict[str, EvalResult]:
    """近 7 天结果缓存（同题同模型取最新一条）。"""
    now = now or datetime.now(UTC)
    since = now - CACHE_TTL
    results = await repo.list_results_since(since)
    cache: dict[str, EvalResult] = {}
    for r in sorted(results, key=lambda x: x.created_at):
        cache[_cache_key(r.task_id, r.model)] = r
    return cache


async def execute_run(
    repo: EvalRepository,
    run: EvalRun,
    tasks: list[EvalTask],
    provider: EvalProvider,
    *,
    now: datetime | None = None,
) -> RunOutcome:
    """执行一次跑批。可被后台任务调用，也可在测试中直接 await。"""
    now = now or datetime.now(UTC)
    started = now
    await repo.update_run(run.id, status="running", started_at=started)

    cache = await build_cache(repo, now=now)
    cost_used = 0.0
    executed = 0
    cached = 0
    results: list[EvalResult] = []
    aborted: str | None = None

    for task in tasks:
        for model in run.models:
            hit = cache.get(_cache_key(task.id, model))
            if hit is not None:
                results.append(
                    EvalResult(
                        id=new_id(),
                        run_id=run.id,
                        task_id=task.id,
                        week=run.week,
                        dimension=task.dimension,
                        model=model,
                        score=float(hit.score),
                        cost_usd=0,
                        latency_ms=0,
                        raw_ref=f"cache://{hit.id}",
                    )
                )
                cached += 1
                continue

            projected = cost_used + 0.02  # 单题成本上界，先卡预算再调用
            if projected > float(run.cost_cap_usd):
                aborted = "cost_cap_exceeded"
                break

            outcome = await provider.score(task, model)
            cost_used = round(cost_used + outcome.cost_usd, 6)
            results.append(
                EvalResult(
                    id=new_id(),
                    run_id=run.id,
                    task_id=task.id,
                    week=run.week,
                    dimension=task.dimension,
                    model=model,
                    score=round(outcome.score, 2),
                    cost_usd=outcome.cost_usd,
                    latency_ms=outcome.latency_ms,
                    raw_ref=outcome.raw_ref,
                )
            )
            executed += 1

        if aborted:
            break

    if results:
        await repo.add_results(results)

    status = "aborted" if aborted else "done"
    await repo.update_run(
        run.id,
        status=status,
        cost_used_usd=cost_used,
        finished_at=datetime.now(UTC),
        error=aborted,
    )
    return RunOutcome(
        run_id=run.id,
        status=status,
        executed=executed,
        cached=cached,
        cost_used_usd=cost_used,
        aborted_reason=aborted,
    )


__all__ = [
    "CACHE_TTL",
    "DEFAULT_MODELS",
    "EvalProvider",
    "FakeProvider",
    "ModelScore",
    "RunOutcome",
    "build_cache",
    "execute_run",
]
