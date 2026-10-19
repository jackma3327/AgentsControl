"use client";

/**
 * 目标直建表单：不依赖 AI 三问，也不依赖收件箱。
 *
 * 为什么要有这条路径：三问需要 LITELLM_BASE_URL，环境没配时整条创建链路就断了。
 * 三问仍然保留，但它是"增强入口"，不是唯一入口。
 *
 * 校验只在前端做两件确定的事（标题、验收非空）；其余字段交给后端 schema，
 * 不在页面里复制一份校验规则，避免两处漂移。
 */

import { useState } from "react";

import { Badge, Button, Card, CardHeader, Segmented } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { errorText } from "@/lib/api/errorText";
import { useCreateGoal } from "@/lib/api/hooks";
import type { GoalTrack } from "@/lib/api/types";

const TRACK_OPTIONS = [
  { value: "money", label: "赚钱" },
  { value: "growth", label: "进化" },
] as const;

/** 只列出后端 templates.py 里 status=available 的三个，避免提交 planned 模板。 */
const TEMPLATES: Record<GoalTrack, readonly { value: string; label: string }[]> = {
  money: [
    { value: "", label: "无模板" },
    { value: "A", label: "A · 内容引流" },
  ],
  growth: [
    { value: "", label: "无模板" },
    { value: "G", label: "G · 熵减" },
    { value: "D", label: "D · 阅读与观影" },
  ],
};

function createErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "unauthorized") return "登录已过期，请重新登录后再创建";
    if (error.code === "validation_failed") return "字段不符合后端要求，请检查期限与预算";
    return errorText(error.code, "创建失败，请重试");
  }
  return errorText("network_error");
}

export function GoalCreateForm({ onClose }: { onClose: () => void }) {
  const [track, setTrack] = useState<GoalTrack>("growth");
  const [template, setTemplate] = useState("");
  const [title, setTitle] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [horizon, setHorizon] = useState("");
  const [budgetHours, setBudgetHours] = useState("4");
  const [budgetMoney, setBudgetMoney] = useState("");
  const [touched, setTouched] = useState(false);

  const create = useCreateGoal();

  const titleOk = title.trim().length > 0;
  const acceptanceOk = acceptance.trim().length > 0;
  const valid = titleOk && acceptanceOk;
  const budgetInvalid =
    (track === "growth" && budgetHours !== "" && !Number.isFinite(Number(budgetHours))) ||
    (track === "money" && budgetMoney !== "" && !Number.isFinite(Number(budgetMoney)));

  const changeTrack = (next: GoalTrack) => {
    setTrack(next);
    // 模板与轨道绑定：切轨后旧模板不再适用，直接清空，避免提交一个语义错位的模板
    if (!TEMPLATES[next].some((item) => item.value === template)) setTemplate("");
  };

  const submit = () => {
    setTouched(true);
    if (!valid || budgetInvalid || create.isPending) return;

    create.mutate(
      {
        track,
        template: template || undefined,
        title: title.trim(),
        acceptance: acceptance.trim(),
        horizon: horizon.trim() || null,
        budget_hours: track === "growth" && budgetHours !== "" ? Number(budgetHours) : null,
        budget_money: track === "money" && budgetMoney !== "" ? Number(budgetMoney) : null,
      },
      {
        onSuccess: () => {
          setTitle("");
          setAcceptance("");
          setHorizon("");
          setTemplate("");
          setBudgetHours("4");
          setBudgetMoney("");
          setTouched(false);
          onClose();
        },
      },
    );
  };

  return (
    <Card className="mb-5">
      <CardHeader
        title="新建目标"
        hint="不依赖 AI：把验收标准写清楚就能立项"
        right={<Badge tone="brand">直接创建</Badge>}
      />

      <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
        <div>
          <span className="mb-1 block text-[11px] text-muted">轨道</span>
          <Segmented options={TRACK_OPTIONS} value={track} onChange={changeTrack} />
        </div>

        <div>
          <label htmlFor="goal-template" className="mb-1 block text-[11px] text-muted">
            模板（可选）
          </label>
          <select
            id="goal-template"
            className="input"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
          >
            {TEMPLATES[track].map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        <div className="sm:col-span-2">
          <label htmlFor="goal-title" className="mb-1 block text-[11px] text-muted">
            标题
          </label>
          <input
            id="goal-title"
            className="input"
            value={title}
            maxLength={300}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => setTouched(true)}
            placeholder="一句话说清这件事"
          />
          {touched && !titleOk ? <p className="mt-1 text-[10px] text-danger">标题不能为空</p> : null}
        </div>

        <div className="sm:col-span-2">
          <label htmlFor="goal-acceptance" className="mb-1 block text-[11px] text-muted">
            验收标准（必填）
          </label>
          <textarea
            id="goal-acceptance"
            className="input resize-none"
            rows={2}
            value={acceptance}
            maxLength={2000}
            onChange={(e) => setAcceptance(e.target.value)}
            onBlur={() => setTouched(true)}
            aria-invalid={touched && !acceptanceOk}
            placeholder="做完之后，什么变了？可被验证，不接受“做完了”"
          />
          {touched && !acceptanceOk ? (
            <p className="mt-1 text-[10px] text-danger">写完“什么变了”才能创建</p>
          ) : (
            <p className="mt-1 text-[10px] text-muted">这条写不出来，说明它还只是个念头，先去收件箱。</p>
          )}
        </div>

        <div>
          <label htmlFor="goal-horizon" className="mb-1 block text-[11px] text-muted">
            期限（可选）
          </label>
          <input
            id="goal-horizon"
            className="input"
            value={horizon}
            maxLength={64}
            onChange={(e) => setHorizon(e.target.value)}
            placeholder="14 天 / 2026-11-02"
          />
        </div>

        <div>
          <label htmlFor="goal-budget" className="mb-1 block text-[11px] text-muted">
            {track === "money" ? "预算（元，可选）" : "预算（小时，可选）"}
          </label>
          <input
            id="goal-budget"
            className="input"
            type="number"
            min={0}
            step={track === "money" ? 100 : 1}
            value={track === "money" ? budgetMoney : budgetHours}
            onChange={(e) => (track === "money" ? setBudgetMoney(e.target.value) : setBudgetHours(e.target.value))}
            placeholder={track === "money" ? "6000" : "4"}
          />
          {budgetInvalid ? <p className="mt-1 text-[10px] text-danger">预算必须是数字</p> : null}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3">
        <p className="text-[11px] text-muted">也可以先去收件箱记一条，用三问补全。</p>
        <div className="flex gap-2">
          <Button onClick={onClose} disabled={create.isPending}>
            取消
          </Button>
          <Button variant="primary" size="md" onClick={submit} disabled={!valid || budgetInvalid || create.isPending}>
            {create.isPending ? "创建中…" : "创建目标"}
          </Button>
        </div>
      </div>

      {create.error ? (
        <p role="alert" className="border-t border-danger/20 bg-danger/5 px-4 py-2 text-[11px] text-danger">
          {createErrorText(create.error)}
        </p>
      ) : null}
    </Card>
  );
}
