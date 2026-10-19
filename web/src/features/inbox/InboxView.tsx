"use client";

/**
 * 收件箱：体感必须在 30 秒内被接住。
 *
 * 交互约束：
 * - 输入框自动聚焦；Cmd/Ctrl+Enter 提交；
 * - 提交后不清空历史，新条目插到顶部（连续记录场景）；
 * - 三问用"对话式步骤"展示，不是一张大表单。
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Badge, Button, Card, CardHeader, Segmented } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { errorText } from "@/lib/api/errorText";
import {
  useCreateGoal,
  useCreateIntake,
  useIntakeItems,
  useStructureIntake,
} from "@/lib/api/hooks";
import type { IntakeItem, StructureResult } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";

const STATUS_LABEL: Record<IntakeItem["status"], string> = {
  raw: "待结构化",
  structured: "已结构化",
  promoted: "已转目标",
  archived_interest: "兴趣池",
};

const STATUS_TONE = {
  raw: "warn",
  structured: "brand",
  promoted: "ok",
  archived_interest: "neutral",
} as const;

/** 三问失败：未配置/不可用的 AI 要给可执行的配置项，而不是笼统的"失败"。 */
function structureErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "upstream_error" || error.code === "not_implemented") {
      return "AI 服务未配置或不可用，请检查 LITELLM_BASE_URL";
    }
    if (error.code === "unauthorized") return "登录已过期，请重新登录后再试";
    return errorText(error.code, "三问失败，请重试");
  }
  return errorText("network_error");
}

function captureErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "unauthorized") return "登录已过期，请重新登录后再记录";
    return errorText(error.code, "记录失败，请重试");
  }
  return errorText("network_error");
}

function promoteErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "upstream_error" || error.code === "not_implemented") {
      return "AI 服务未配置或不可用，请检查 LITELLM_BASE_URL";
    }
    if (error.code === "unauthorized") return "登录已过期，请重新登录后再创建";
    if (error.code === "validation_failed") return "验收标准不能为空，或格式不符合要求";
    return errorText(error.code, "创建失败，请重试");
  }
  return errorText("network_error");
}

export function InboxView() {
  const [text, setText] = useState("");
  const [active, setActive] = useState<StructureResult | null>(null);
  const [track, setTrack] = useState<"growth" | "money">("growth");
  const [budgetHours, setBudgetHours] = useState(4);
  const [justSaved, setJustSaved] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const list = useIntakeItems();
  const create = useCreateIntake();
  const structure = useStructureIntake();
  const createGoal = useCreateGoal();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // 连续提交时只保留最后一次的 2 秒反馈，避免多个 timer 叠在一起
  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  const submit = () => {
    const value = text.trim();
    if (!value || create.isPending) return;
    create.mutate(value, {
      onSuccess: () => {
        setText("");
        setJustSaved(true);
        if (savedTimer.current) clearTimeout(savedTimer.current);
        savedTimer.current = setTimeout(() => setJustSaved(false), 2000);
        inputRef.current?.focus();
      },
    });
  };

  return (
    <div className="mx-auto grid max-w-5xl grid-cols-1 gap-5 lg:grid-cols-[1.1fr_0.9fr]">
      <div>
        <header className="mb-4">
          <h1 className="text-xl font-semibold tracking-tight">收件箱</h1>
          <p className="mt-1 text-[12px] text-muted">一句话就够。完整性由 AI 事后补，不占你的带宽。</p>
        </header>

        <Card className="p-3">
          <label htmlFor="capture" className="sr-only">
            记一条
          </label>
          <textarea
            id="capture"
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
            rows={3}
            placeholder="想到什么就写什么……（Cmd/Ctrl + Enter 提交）"
            className="w-full resize-none rounded-md border border-line bg-canvas px-3 py-2 text-[13px] outline-none focus:border-brand"
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-[11px] text-muted">30 秒内可完成 · 不要求完整</span>
            <Button
              variant="primary"
              size="md"
              onClick={submit}
              disabled={(!text.trim() && !justSaved) || create.isPending}
            >
              {create.isPending ? "记录中…" : justSaved ? "已记下 ✓" : "记下来"}
            </Button>
          </div>
          <span className="sr-only" role="status" aria-live="polite">
            {justSaved ? "已记下" : ""}
          </span>
          {create.error ? (
            <p role="alert" className="mt-2 text-[11px] text-danger">
              {captureErrorText(create.error)}
            </p>
          ) : null}
        </Card>

        <Card className="mt-4">
          <CardHeader title="最近记录" hint="从新到旧；超过 14 天未结构化会自动进兴趣池" />
          <AsyncBoundary
            isLoading={list.isLoading}
            error={list.error}
            isEmpty={(list.data?.items.length ?? 0) === 0}
            onRetry={() => void list.refetch()}
            emptyTitle="还没有记录"
            emptyHint="把刚冒出来的那个念头写进来，30 秒就能完成。"
          >
            <ul className="divide-y divide-line">
              {list.data?.items.map((item) => (
                <li key={item.id} className="flex items-start justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <div className="text-[13px] leading-snug">{item.text}</div>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-muted">
                      <Badge tone={STATUS_TONE[item.status]}>{STATUS_LABEL[item.status]}</Badge>
                      <span>{timeAgo(item.created_at)}</span>
                      {item.structured?.acceptance ? (
                        <span className="truncate">验收：{item.structured.acceptance}</span>
                      ) : null}
                    </div>
                  </div>
                  {item.status === "raw" ? (
                    <Button
                      onClick={() => structure.mutate(item.id, { onSuccess: (r) => setActive(r) })}
                      disabled={structure.isPending}
                    >
                      三问
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          </AsyncBoundary>
        </Card>
      </div>

      <Card className="h-fit">
        <CardHeader
          title="三问 → 目标卡"
          hint="只问三个问题，答不出第一问就进兴趣池"
        />
        {structure.error ? (
          <div role="alert" className="px-4 py-6">
            <div className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-[12px] leading-relaxed text-danger">
              {structureErrorText(structure.error)}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-muted">
              修好配置后重新点「三问」即可；也可以去「目标」页直接新建，不必等 AI。
            </p>
          </div>
        ) : active === null ? (
          <div className="px-4 py-8 text-center text-[12px] leading-relaxed text-muted">
            点左侧任意「三问」，AI 会把它变成一张可验收的目标卡。
            <br />
            这一栏不是表单，是一条对话。
          </div>
        ) : (
          <div className="space-y-4 px-4 py-4">
            <Question index={1} label="做完之后，什么变了？">
              {active.structured.acceptance ?? "AI 也答不出——建议先放进兴趣池，不要硬造目标"}
            </Question>
            <Question index={2} label="多久内想看到结果？">
              {active.structured.horizon ?? "未给出期限"}
            </Question>
            <Question index={3} label="愿意为它花多少？">
              {active.structured.budget ?? "未给出预算"}
            </Question>

            {active.structured.suggest_interest ? (
              <div className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-[12px] text-warn">
                第一问答不出来。按设计，它应该进兴趣池，而不是变成目标。
              </div>
            ) : (
              <div className="space-y-3 border-t border-line pt-3">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] text-muted">轨道</span>
                  <Segmented
                    value={track}
                    onChange={setTrack}
                    options={[
                      { value: "growth", label: "进化" },
                      { value: "money", label: "赚钱" },
                    ]}
                  />
                </div>
                {track === "growth" ? (
                  <label className="flex items-center justify-between text-[12px] text-muted">
                    预算小时
                    <input
                      type="number"
                      min={1}
                      value={budgetHours}
                      onChange={(e) => setBudgetHours(Number(e.target.value))}
                      className="w-20 rounded border border-line bg-canvas px-2 py-1 text-ink"
                    />
                  </label>
                ) : null}
                <Button
                  variant="primary"
                  size="md"
                  className="w-full"
                  disabled={createGoal.isPending || !active.structured.acceptance}
                  onClick={() => {
                    createGoal.mutate(
                      {
                        track,
                        title: list.data?.items.find((i) => i.id === active.item_id)?.text.slice(0, 120) ?? "未命名目标",
                        acceptance: active.structured.acceptance ?? "",
                        horizon: active.structured.horizon,
                        budget_hours: track === "growth" ? budgetHours : null,
                        intake_item_id: active.item_id,
                      },
                      { onSuccess: () => setActive(null) },
                    );
                  }}
                >
                  {createGoal.isPending ? "创建中…" : "转成目标卡"}
                </Button>
                {!active.structured.acceptance ? (
                  <p className="text-[11px] text-warn">三问没给出验收标准：先放进兴趣池，或去目标页直接写。</p>
                ) : null}
                {createGoal.error ? (
                  <p role="alert" className="text-[11px] text-danger">
                    {promoteErrorText(createGoal.error)}
                  </p>
                ) : null}
              </div>
            )}
            <p className="text-[10px] text-muted">
              model {active.model} · 本次 ¥{active.cost_usd.toFixed(4)}
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}

function Question({ index, label, children }: { index: number; label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-medium text-brand">Q{index}</div>
      <div className="mt-0.5 text-[12px] text-muted">{label}</div>
      <div className="mt-1 rounded-md bg-canvas px-3 py-2 text-[13px] leading-relaxed">{children}</div>
    </div>
  );
}
