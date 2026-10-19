"use client";

/**
 * 今日页：一天只从这里开始。
 *
 * 产品约束（plan/W10.md §2）：
 * - 顶部只有 3 个数字，不加图表；
 * - 最多 8 条决策卡，超出显示"还有 N 条，明天再说"；
 * - J/K 切换、Enter 通过、X 拒绝；决策后乐观更新。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Badge, Button, Card, CardHeader, EmptyState, StatTile } from "@/components/ui";
import { useApprovalQueue, useBandwidth, useDecideApproval, useLedger } from "@/lib/api/hooks";
import type { Approval } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";

const MAX_CARDS = 8;

export function TodayView() {
  const queue = useApprovalQueue();
  const bandwidth = useBandwidth();
  const ledger = useLedger();
  const decide = useDecideApproval();

  const items = useMemo(() => (queue.data?.items ?? []).slice(0, MAX_CARDS), [queue.data]);
  const overflow = Math.max(0, (queue.data?.count ?? 0) - MAX_CARDS);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    if (cursor >= items.length) setCursor(Math.max(0, items.length - 1));
  }, [items.length, cursor]);

  const act = useCallback(
    (approval: Approval, decision: "approved" | "rejected") => {
      decide.mutate({ id: approval.id, decision });
    },
    [decide],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "j" || event.key === "J") {
        setCursor((c) => Math.min(items.length - 1, c + 1));
      } else if (event.key === "k" || event.key === "K") {
        setCursor((c) => Math.max(0, c - 1));
      } else if (event.key === "Enter" || event.key === "x" || event.key === "X") {
        const current = items[cursor];
        if (current) act(current, event.key === "Enter" ? "approved" : "rejected");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, cursor, items]);

  const attention = ledger.data?.attention;
  const decisionsToday = bandwidth.data?.input.decisions_today ?? 0;
  const limit = bandwidth.data?.config.daily_decision_limit ?? 12;
  const defer = bandwidth.data?.verdict.defer_non_critical ?? false;
  const exhausted = defer || decisionsToday >= limit;

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-5 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">今日</h1>
          <p className="mt-1 text-[12px] text-muted">
            {new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}
            {" · "}只做非你不可的事
          </p>
        </div>
        <div className="text-[11px] text-muted">
          <kbd className="rounded border border-line px-1">J</kbd>/
          <kbd className="rounded border border-line px-1">K</kbd> 切换 ·{" "}
          <kbd className="rounded border border-line px-1">Enter</kbd> 通过 ·{" "}
          <kbd className="rounded border border-line px-1">X</kbd> 拒绝
        </div>
      </header>

      <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile
          label="决策负载"
          value={`${decisionsToday}/${limit}`}
          hint={exhausted ? "已到今日上限" : bandwidth.data ? "L0+L1 今日已做决策" : "加载中"}
          tone={exhausted ? "danger" : decisionsToday > limit * 0.7 ? "warn" : "neutral"}
        />
        <StatTile
          label="注意力余额"
          value={attention?.minutes != null ? `${Math.max(0, 180 - attention.minutes)} min` : "未开始记录"}
          hint={attention?.min_per_outcome != null ? `${attention.min_per_outcome} min/产出` : "账本无产出数据"}
          tone={attention?.minutes != null && attention.minutes > 150 ? "warn" : "neutral"}
        />
        <StatTile
          label="异常"
          value={queue.data?.count ?? "—"}
          hint="待你决策的 L0/L1 事项"
          tone={(queue.data?.count ?? 0) > 0 ? "danger" : "ok"}
        />
      </div>

      {bandwidth.data?.verdict.reasons.length ? (
        <div className="mb-4 rounded-card border border-warn/30 bg-warn/5 px-4 py-3 text-[12px] text-warn">
          {bandwidth.data.verdict.reasons.join("；")}
        </div>
      ) : null}

      <Card>
        <CardHeader
          title="需要你决策"
          hint="机器已经初审过，只把不确定的递上来"
          right={<Badge tone="brand">{queue.data?.count ?? 0} 条</Badge>}
        />
        <AsyncBoundary
          isLoading={queue.isLoading}
          error={queue.error}
          isEmpty={items.length === 0}
          onRetry={() => void queue.refetch()}
          emptyTitle="今天没有需要你决策的事"
          emptyHint="这是系统正常运转的样子：L2/L3 已经被机器吃掉。"
        >
          <ul className="divide-y divide-line">
            {items.map((approval, index) => (
              <li
                key={approval.id}
                className={`px-4 py-3 ${index === cursor ? "bg-brand/5" : ""}`}
                onMouseEnter={() => setCursor(index)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge tone={approval.level === "L0" ? "danger" : "warn"}>{approval.level}</Badge>
                      <span className="truncate text-[13px] font-medium">{approval.title}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-muted">
                      创建于 {timeAgo(approval.created_at)}
                      {approval.held_reason ? ` · ${approval.held_reason}` : ""}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <Button variant="primary" onClick={() => act(approval, "approved")} disabled={decide.isPending}>
                      通过
                    </Button>
                    <Button variant="danger" onClick={() => act(approval, "rejected")} disabled={decide.isPending}>
                      拒绝
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </AsyncBoundary>
        {overflow > 0 || exhausted ? (
          <div className="border-t border-line px-4 py-2.5 text-[12px] text-muted">
            {overflow > 0 ? `还有 ${overflow} 条，明天再说。` : "今天到此为止。"}
          </div>
        ) : null}
      </Card>

      {decide.error ? (
        <div className="mt-3 text-[12px] text-danger">决策失败，请刷新后重试（状态可能已变化）</div>
      ) : null}

      {items.length === 0 && queue.data?.count === 0 ? (
        <div className="mt-4">
          <EmptyState title="带宽还有余量" hint="去收件箱把体感记下来，别让它在 30 秒内消失。" />
        </div>
      ) : null}
    </div>
  );
}
