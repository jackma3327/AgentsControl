"use client";

/**
 * 兵工厂：现在能用什么、贵不贵、哪类任务根本做不到。
 *
 * 页面主次：
 * 1. 雷达图（8 维）+ 模型选择
 * 2. 能力上限（blocked/risky 必须带人话解释）
 * 3. skill 版本与漂移告警
 */

import { useState } from "react";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { RadarChart } from "@/components/charts/RadarChart";
import { Badge, Button, Card, CardHeader, Segmented } from "@/components/ui";
import { useCeiling, useDrift, useRadar, useRunEvals, useSkills } from "@/lib/api/hooks";
import { percent } from "@/lib/format";

const CEILING_TONE = {
  blocked: "danger",
  risky: "warn",
  ok: "ok",
  insufficient: "neutral",
} as const;

export function ArsenalView() {
  const [week, setWeek] = useState<string | undefined>(undefined);
  const [model, setModel] = useState<string>("");
  const radar = useRadar(week);
  const ceiling = useCeiling(week);
  const skills = useSkills();
  const drift = useDrift();
  const run = useRunEvals();

  const models = radar.data?.models ?? [];
  const selected = model || models[0] || "";

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">兵工厂</h1>
          <p className="mt-1 text-[12px] text-muted">
            要回答的不是"哪个模型最强"，是"哪类任务现在根本做不到，所以别立项"。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="neutral">{radar.data?.week ?? "本周"}</Badge>
          <Button
            variant="primary"
            onClick={() => run.mutate(models.length ? models : ["claude-opus-4.5", "gpt-5.1", "gemini-3-pro"])}
            disabled={run.isPending}
          >
            {run.isPending ? "已提交…" : "跑一轮评测"}
          </Button>
        </div>
      </header>

      {run.data ? (
        <div className="mb-4 rounded-card border border-brand/20 bg-brand/5 px-4 py-2 text-[12px] text-brand">
          批次 {run.data.id.slice(0, 8)} 已创建（{run.data.task_count} 题 × {run.data.models.length} 模型，
          上限 ¥{run.data.cost_cap_usd}）。完成后刷新本页看雷达。
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[0.95fr_1.05fr]">
        <Card>
          <CardHeader
            title="能力雷达"
            hint="每维取最优模型；缺样本不按 0 分算"
            right={
              models.length > 1 ? (
                <Segmented
                  value={selected}
                  onChange={setModel}
                  options={models.slice(0, 5).map((m) => ({ value: m, label: m.split("-")[0] ?? m }))}
                />
              ) : null
            }
          />
          <div className="p-3">
            <AsyncBoundary
              isLoading={radar.isLoading}
              error={radar.error}
              isEmpty={(radar.data?.dimensions.length ?? 0) === 0}
              onRetry={() => void radar.refetch()}
              emptyTitle="还没有评测数据"
              emptyHint="点右上「跑一轮评测」。20 题 × 5 模型，一次约 10 分钟。"
            >
              <RadarChart
                dimensions={radar.data?.dimensions ?? []}
                models={models}
                selected={selected || undefined}
              />
            </AsyncBoundary>
          </div>
        </Card>

        <Card>
          <CardHeader title="能力上限" hint="标红的维度不要立项；标黄的需要拆步骤 + 人审" />
          <AsyncBoundary
            isLoading={ceiling.isLoading}
            error={ceiling.error}
            isEmpty={(ceiling.data?.dimensions.length ?? 0) === 0}
            onRetry={() => void ceiling.refetch()}
            emptyTitle="暂无判定"
            emptyHint="需要先有本周评测结果。"
          >
            <ul className="divide-y divide-line">
              {ceiling.data?.dimensions.map((dim) => (
                <li key={dim.dimension} className="px-4 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] font-medium">{dim.label}</span>
                    <Badge tone={CEILING_TONE[dim.status]}>
                      {dim.best_score != null ? `${dim.best_score.toFixed(1)} 分` : "缺样本"}
                    </Badge>
                  </div>
                  <p
                    className={`mt-1 text-[11px] leading-relaxed ${
                      dim.status === "blocked" ? "text-danger" : dim.status === "risky" ? "text-warn" : "text-muted"
                    }`}
                  >
                    {dim.advice}
                  </p>
                </li>
              ))}
            </ul>
          </AsyncBoundary>
        </Card>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="skill 版本"
            hint="没有 eval set 的 skill 不许上线"
            right={<Badge tone="neutral">{skills.data?.length ?? 0}</Badge>}
          />
          <AsyncBoundary
            isLoading={skills.isLoading}
            error={skills.error}
            isEmpty={(skills.data?.length ?? 0) === 0}
            onRetry={() => void skills.refetch()}
            emptyTitle="还没有登记 skill"
            emptyHint="skill 的三件套：触发条件 / 判据 / 反例，缺一不可。"
          >
            <ul className="divide-y divide-line">
              {skills.data?.map((skill) => (
                <li key={skill.id} className="flex items-center justify-between px-4 py-2.5">
                  <div>
                    <div className="text-[12px] font-medium">
                      {skill.name} <span className="text-muted">v{skill.version}</span>
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted">
                      eval 集 {skill.eval_task_ids.length} 题 · 灰度 {skill.rollout_pct}%
                    </div>
                  </div>
                  <Badge
                    tone={
                      skill.status === "active" ? "ok" : skill.status === "draft" ? "neutral" : skill.status === "stale" ? "danger" : "warn"
                    }
                  >
                    {skill.status}
                  </Badge>
                </li>
              ))}
            </ul>
          </AsyncBoundary>
        </Card>

        <Card>
          <CardHeader title="漂移告警" hint="同模型同维度周环比降 >3pct（上游悄悄降级的唯一信号）" />
          <AsyncBoundary
            isLoading={drift.isLoading}
            error={drift.error}
            isEmpty={(drift.data?.length ?? 0) === 0}
            onRetry={() => void drift.refetch()}
            emptyTitle="没有漂移"
            emptyHint="本周没有模型出现 >3pct 的下降。"
          >
            <ul className="divide-y divide-line">
              {drift.data?.map((alert) => (
                <li key={alert.id} className="flex items-center justify-between px-4 py-2.5">
                  <div>
                    <div className="text-[12px] font-medium">
                      {alert.model} · {alert.dimension}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted">
                      {alert.prev_week} {alert.prev_score} → {alert.week} {alert.curr_score}
                    </div>
                  </div>
                  <Badge tone={alert.delta_pct <= -5 ? "danger" : "warn"}>{percent(alert.delta_pct, 1)}</Badge>
                </li>
              ))}
            </ul>
          </AsyncBoundary>
        </Card>
      </div>
    </div>
  );
}
