"use client";

/**
 * 执行页：默认只看异常。
 * 正常任务折叠成一行统计——"47 个正常 / 3 个卡住"比 50 行列表有用。
 * 任务模块（W03）未合并时，这里不造假数据，直接说明"等哪个窗口"。
 */

import Link from "next/link";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Badge, Card, CardHeader, StatTile } from "@/components/ui";
import { useTasks } from "@/lib/api/hooks";
import { timeAgo } from "@/lib/format";

export function ExecutionView() {
  const tasks = useTasks();
  const rows = tasks.data?.items ?? [];
  const abnormal = rows.filter((t) => !["done", "queued"].includes(t.state));
  const normal = rows.length - abnormal.length;

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-4">
        <h1 className="text-xl font-semibold tracking-tight">执行</h1>
        <p className="mt-1 text-[12px] text-muted">只看异常。正常任务不该占用你的注意力。</p>
      </header>

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile label="正常" value={rows.length ? normal : "—"} hint="运行中 / 排队中" tone="ok" />
        <StatTile
          label="异常"
          value={rows.length ? abnormal.length : "—"}
          hint="卡住 / 失败 / 超预算"
          tone={abnormal.length ? "danger" : "neutral"}
        />
        <StatTile label="死信" value={rows.filter((t) => t.state === "dead").length} hint="三次回收仍失败" tone="danger" />
      </div>

      <Card>
        <CardHeader
          title="异常任务"
          hint="点开看最后 5 条事件与所属会话"
          right={<Badge tone={abnormal.length ? "danger" : "ok"}>{abnormal.length}</Badge>}
        />
        <AsyncBoundary
          isLoading={tasks.isLoading}
          error={tasks.error}
          isEmpty={abnormal.length === 0}
          onRetry={() => void tasks.refetch()}
          emptyTitle={tasks.error ? "任务模块尚未接入" : "现在没有异常任务"}
          emptyHint={
            tasks.error
              ? "调度面（W03 任务队列与租约）合并后，这里会显示卡住/失败/超预算的任务；在此之前不展示假数据。"
              : `正常任务 ${normal} 个已折叠。`
          }
        >
          <ul className="divide-y divide-line">
            {abnormal.slice(0, 20).map((task) => (
              <li key={task.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-[13px]">{task.title}</div>
                  <div className="mt-0.5 text-[11px] text-muted">
                    {task.state} · 更新于 {timeAgo(task.updated_at)}
                  </div>
                </div>
                <Badge tone="danger">{task.state}</Badge>
              </li>
            ))}
          </ul>
        </AsyncBoundary>
      </Card>

      <p className="mt-4 text-[11px] text-muted">
        会话视图由 Langfuse 提供（按 task_id 深链）；<Link className="text-brand" href="/projects">项目视图</Link>{" "}
        里能看到它占用了哪段时间。
      </p>
    </div>
  );
}
