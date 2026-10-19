"use client";

/**
 * 目标页：双轨分栏。
 *
 * 每张卡必须能回答三件事：预算用掉多少、还剩几天、是否触发止损。
 * 止损不是红字，是行动按钮（冻结 / 降级）——只显示状态等于把决策又推回给人。
 */

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Badge, Button, Card, CardHeader, EmptyState, ProgressBar, StatTile } from "@/components/ui";
import { useBudgetStatus, useGoals, usePatchGoal } from "@/lib/api/hooks";
import type { Goal, GoalTrack } from "@/lib/api/types";
import { daysLeft, hours, shortDate, yuan } from "@/lib/format";

const TRACK_TITLE: Record<GoalTrack, { title: string; hint: string }> = {
  money: { title: "赚钱", hint: "验收看北极星，止损看产出是否低于 P25" },
  growth: { title: "进化", hint: "验收物是认知资产；无产出物不算完成" },
};

export function GoalsView() {
  const goals = useGoals();
  const rows = goals.data ?? [];
  const money = rows.filter((g) => g.track === "money");
  const growth = rows.filter((g) => g.track === "growth");
  const atRisk = rows.filter((g) => g.stop_loss_triggered_at).length;

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">目标</h1>
          <p className="mt-1 text-[12px] text-muted">两条轨的验收方式不同，不要用同一套 KPI 压进化目标。</p>
        </div>
        <div className="text-[11px] text-muted">
          共 {rows.length} 个 · 触发止损 {atRisk}
        </div>
      </header>

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="赚钱轨" value={money.length} hint="进行中" />
        <StatTile label="进化轨" value={growth.length} hint="进行中" />
        <StatTile label="已冻结" value={rows.filter((g) => g.status === "frozen").length} hint="止损或手动" />
        <StatTile label="触发止损" value={atRisk} tone={atRisk ? "danger" : "ok"} hint="需要做处置决定" />
      </div>

      <AsyncBoundary
        isLoading={goals.isLoading}
        error={goals.error}
        isEmpty={rows.length === 0}
        onRetry={() => void goals.refetch()}
        emptyTitle="还没有目标"
        emptyHint="目标来自体感：先去收件箱记一条，再用三问把它变成契约。"
      >
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {(["money", "growth"] as const).map((track) => (
            <Card key={track}>
              <CardHeader
                title={TRACK_TITLE[track].title}
                hint={TRACK_TITLE[track].hint}
                right={<Badge tone="neutral">{(track === "money" ? money : growth).length}</Badge>}
              />
              <div className="space-y-3 p-3">
                {(track === "money" ? money : growth).length === 0 ? (
                  <EmptyState title={`${TRACK_TITLE[track].title}轨还没有目标`} />
                ) : (
                  (track === "money" ? money : growth).map((goal) => <GoalCard key={goal.id} goal={goal} />)
                )}
              </div>
            </Card>
          ))}
        </div>
      </AsyncBoundary>
    </div>
  );
}

function GoalCard({ goal }: { goal: Goal }) {
  const budget = useBudgetStatus(goal.id);
  const patch = usePatchGoal();
  const days = daysLeft(goal.horizon_at);
  const stop = Boolean(goal.stop_loss_triggered_at) || goal.status === "frozen";
  const cap = goal.track === "money" ? goal.budget_money : goal.budget_hours;
  const spent = goal.track === "money" ? (budget.data?.spent ?? 0) : null;
  const remaining = budget.data?.remaining ?? null;

  return (
    <article className={`rounded-md border p-3 ${stop ? "border-danger/30 bg-danger/5" : "border-line"}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[11px] text-muted">
            {goal.code} · {goal.track === "money" ? "赚钱" : "进化"}
            {goal.template ? ` · 模板 ${goal.template}` : ""}
          </div>
          <h3 className="mt-0.5 text-[13px] font-medium leading-snug">{goal.title}</h3>
        </div>
        <Badge tone={goal.status === "active" ? "ok" : goal.status === "frozen" ? "danger" : "neutral"}>
          {goal.status}
        </Badge>
      </div>

      <p className="mt-2 line-clamp-2 text-[12px] leading-relaxed text-muted">验收：{goal.acceptance}</p>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <dt className="text-muted">预算</dt>
          <dd className="mt-0.5 font-medium">
            {goal.track === "money" ? yuan(cap) : hours(cap)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">已用</dt>
          <dd className="mt-0.5 font-medium">
            {goal.track === "money" ? yuan(spent) : "—"}
            {remaining != null ? <span className="ml-1 text-[10px] text-muted">余 {yuan(remaining)}</span> : null}
          </dd>
        </div>
        <div>
          <dt className="text-muted">剩余天数</dt>
          <dd className={`mt-0.5 font-medium ${days != null && days <= 3 ? "text-danger" : ""}`}>
            {days != null ? `${days} 天` : shortDate(goal.horizon_at)}
          </dd>
        </div>
      </dl>

      {goal.track === "money" && cap ? (
        <div className="mt-2">
          <ProgressBar
            value={spent ?? 0}
            max={cap}
            tone={Boolean(spent != null && cap && spent / cap > 0.9) ? "danger" : "brand"}
          />
        </div>
      ) : null}

      {stop ? (
        <div className="mt-3 flex items-center justify-between gap-2 rounded bg-surface px-2 py-2">
          <span className="text-[11px] text-danger">
            {goal.status === "frozen" ? "已冻结，等你的处置" : "已触发止损"}
          </span>
          <div className="flex gap-1.5">
            <Button
              variant="ghost"
              onClick={() => patch.mutate({ id: goal.id, status: "frozen" })}
              disabled={patch.isPending || goal.status === "frozen"}
            >
              冻结
            </Button>
            <Button
              variant="danger"
              onClick={() => patch.mutate({ id: goal.id, status: "degraded" })}
              disabled={patch.isPending}
            >
              降级为兴趣
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}
