"use client";

/**
 * 总视图：一张卡片墙 + 设备热力 + 待决策队列。
 * 目的一眼看出"哪个项目在耗钱、哪台设备可疑、哪件事在等我"。
 */

import Link from "next/link";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Badge, Card, CardHeader, StatTile } from "@/components/ui";
import { useApprovalQueue, useCeiling, useGoals, useLedger } from "@/lib/api/hooks";
import { percent, yuan } from "@/lib/format";

const DEVICE_SLOTS = [
  { name: "Mac 主机", state: "unknown" },
  { name: "Mac mini", state: "unknown" },
  { name: "笔记本", state: "unknown" },
  { name: "VPS", state: "unknown" },
  { name: "第三方", state: "unknown" },
] as const;

export function OverviewView() {
  const goals = useGoals();
  const approvals = useApprovalQueue();
  const ceiling = useCeiling();
  const ledger = useLedger();

  const rows = goals.data ?? [];
  const money = rows.filter((g) => g.track === "money").length;
  const growth = rows.filter((g) => g.track === "growth").length;
  const blocked = ceiling.data?.blocked.length ?? 0;
  const attentionText =
    ledger.data?.attention.min_per_outcome != null
      ? `${ledger.data.attention.min_per_outcome.toFixed(1)} min/产出`
      : "注意力：未开始记录";
  const topDimension = ceiling.data?.dimensions
    .filter((d) => d.best_score != null)
    .sort((a, b) => (b.best_score ?? 0) - (a.best_score ?? 0))[0]?.label;
  const blockedText = ceiling.data?.blocked.length ? ceiling.data.blocked.join("、") : "无";
  const riskyText = ceiling.data?.risky.length ? " · 有风险：" + ceiling.data.risky.join("、") : "";
  const topText = topDimension ? " · 最强维度 " + topDimension : "";

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-4">
        <h1 className="text-xl font-semibold tracking-tight">总视图</h1>
        <p className="mt-1 text-[12px] text-muted">所有项目、所有设备、所有待决策，压在一屏里。</p>
      </header>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="赚钱轨目标" value={money} hint="在跑" />
        <StatTile label="进化轨目标" value={growth} hint="在跑" />
        <StatTile
          label="待决策"
          value={approvals.data?.count ?? "—"}
          hint="L0/L1"
          tone={(approvals.data?.count ?? 0) > 0 ? "warn" : "ok"}
        />
        <StatTile
          label="能力受阻维度"
          value={blocked}
          hint="全模型 <70 分"
          tone={blocked ? "danger" : "ok"}
        />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.2fr_0.8fr]">
        <Card>
          <CardHeader
            title="项目卡片墙"
            hint="目标即项目；卡片上的红灯是止损，不是装饰"
            right={<Badge tone="neutral">{rows.length}</Badge>}
          />
          <AsyncBoundary
            isLoading={goals.isLoading}
            error={goals.error}
            isEmpty={rows.length === 0}
            onRetry={() => void goals.refetch()}
            emptyTitle="还没有项目"
            emptyHint="去收件箱记一条体感，三问之后就是一张项目卡。"
          >
            <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2">
              {rows.slice(0, 8).map((goal) => {
                const stop = Boolean(goal.stop_loss_triggered_at) || goal.status === "frozen";
                return (
                  <Link
                    key={goal.id}
                    href="/goals"
                    className={`rounded-md border p-3 transition-colors hover:bg-canvas ${
                      stop ? "border-danger/30" : "border-line"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-[12px] font-medium leading-snug">{goal.title}</span>
                      <span
                        className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                          stop ? "bg-danger" : goal.status === "active" ? "bg-ok" : "bg-line"
                        }`}
                        aria-label={stop ? "止损" : goal.status}
                      />
                    </div>
                    <div className="mt-2 flex items-center justify-between text-[10px] text-muted">
                      <span>{goal.track === "money" ? "赚钱" : "进化"}</span>
                      <span>
                        {goal.track === "money" ? yuan(goal.budget_money) : `${goal.budget_hours ?? "—"} h`}
                      </span>
                    </div>
                  </Link>
                );
              })}
            </div>
          </AsyncBoundary>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader
              title="设备热力"
              hint="绿=在线，黄=可疑，红=离线"
              right={<Badge tone="warn">W04 未接入</Badge>}
            />
            <div className="grid grid-cols-5 gap-2 p-3">
              {DEVICE_SLOTS.map((slot) => (
                <div
                  key={slot.name}
                  className="flex h-16 flex-col items-center justify-center rounded border border-dashed border-line bg-canvas text-center"
                  title="设备接入（W04）合并后显示真实状态"
                >
                  <span className="text-[10px] text-muted">{slot.name}</span>
                  <span className="mt-1 text-[10px] text-muted">未知</span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader
              title="待决策"
              hint="只列 3 条；其余在今日页"
              right={<Link href="/today" className="text-[11px] text-brand">去处理</Link>}
            />
            <ul className="divide-y divide-line">
              {(approvals.data?.items ?? []).slice(0, 3).map((item) => (
                <li key={item.id} className="flex items-center justify-between gap-2 px-4 py-2.5">
                  <span className="truncate text-[12px]">{item.title}</span>
                  <Badge tone={item.level === "L0" ? "danger" : "warn"}>{item.level}</Badge>
                </li>
              ))}
            </ul>
            {(approvals.data?.items.length ?? 0) === 0 ? (
              <p className="px-4 py-4 text-[11px] text-muted">队列清空。系统正在正常运转。</p>
            ) : null}
          </Card>

          <Card className="p-4">
            <div className="flex items-center justify-between">
              <span className="text-[12px] font-medium">本期支出</span>
              <span className="text-[13px] font-semibold tabular-nums">
                {ledger.data ? yuan(ledger.data.total) : "—"}
              </span>
            </div>
            <div className="mt-1 text-[11px] text-muted">{attentionText}</div>
          </Card>
        </div>
      </div>

      {ceiling.data && ceiling.data.dimensions.length > 0 ? (
        <p className="mt-4 text-[11px] text-muted">
          {"能力受阻：" + blockedText + riskyText + topText}
        </p>
      ) : null}
    </div>
  );
}
