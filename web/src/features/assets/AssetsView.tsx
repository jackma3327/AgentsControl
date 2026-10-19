"use client";

/**
 * 资产页：skill / harness / 认知卡。
 *
 * 认知卡是进化轨目标的验收物：**复用一次才算完成**，
 * 所以卡片上最重要的按钮是"用一次"，不是"编辑"。
 */

import { useState, type ReactNode } from "react";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Badge, Button, Card, CardHeader, Segmented } from "@/components/ui";
import { useCards, useCreateCard, useHarness, useReuseCard, useSkills } from "@/lib/api/hooks";
import { shortDate } from "@/lib/format";

type Tab = "cards" | "skills" | "harness";

const TABS = [
  { value: "cards", label: "认知卡" },
  { value: "skills", label: "skill" },
  { value: "harness", label: "harness" },
] as const;

export function AssetsView() {
  const [tab, setTab] = useState<Tab>("cards");
  const [form, setForm] = useState({
    title: "",
    trigger: "",
    rule: "",
    counterexample: "",
    confidence: 60,
  });

  const cards = useCards();
  const skills = useSkills();
  const harness = useHarness();
  const create = useCreateCard();
  const reuse = useReuseCard();

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">资产</h1>
          <p className="mt-1 text-[12px] text-muted">
            我变强了吗？看的是可复用资产的数量与被复用次数，不是读了多少。
          </p>
        </div>
        <Segmented options={TABS} value={tab} onChange={setTab} />
      </header>

      {tab === "cards" ? (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.15fr_0.85fr]">
          <Card>
            <CardHeader
              title="认知卡"
              hint="触发条件 / 判断规则 / 反例 / 置信度 / 失效期"
              right={<Badge tone="neutral">{cards.data?.length ?? 0}</Badge>}
            />
            <AsyncBoundary
              isLoading={cards.isLoading}
              error={cards.error}
              isEmpty={(cards.data?.length ?? 0) === 0}
              onRetry={() => void cards.refetch()}
              emptyTitle="还没有认知卡"
              emptyHint="读完一本书不是产出，一张能被复用的卡才是。"
            >
              <ul className="divide-y divide-line">
                {cards.data?.map((card) => (
                  <li key={card.id} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium">{card.title}</div>
                        <div className="mt-1 line-clamp-2 text-[11px] text-muted">
                          触发：{card.trigger} · 规则：{card.rule}
                        </div>
                        <div className="mt-1 text-[10px] text-muted">
                          置信度 {card.confidence} · 已用 {card.reuse_count} 次 · 失效{" "}
                          {shortDate(card.expires_at)}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Badge tone={card.reuse_count > 0 ? "ok" : "warn"}>
                          {card.reuse_count > 0 ? "已完成" : "待复用"}
                        </Badge>
                        <Button onClick={() => reuse.mutate(card.id)} disabled={reuse.isPending}>
                          用一次
                        </Button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </AsyncBoundary>
          </Card>

          <Card>
            <CardHeader title="新建一张卡" hint="写不出反例，说明这条规则还没想清" />
            <div className="space-y-2.5 p-4">
              <Field label="标题">
                <input
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                  className="input"
                  placeholder="例：复杂系统崩在耦合处"
                />
              </Field>
              <Field label="触发条件">
                <textarea
                  value={form.trigger}
                  onChange={(e) => setForm({ ...form, trigger: e.target.value })}
                  className="input"
                  rows={2}
                  placeholder="什么情况下该想起这条规则"
                />
              </Field>
              <Field label="判断规则">
                <textarea
                  value={form.rule}
                  onChange={(e) => setForm({ ...form, rule: e.target.value })}
                  className="input"
                  rows={2}
                />
              </Field>
              <Field label="反例">
                <textarea
                  value={form.counterexample}
                  onChange={(e) => setForm({ ...form, counterexample: e.target.value })}
                  className="input"
                  rows={2}
                  placeholder="规则失效的情形"
                />
              </Field>
              <Field label={`置信度 ${form.confidence}`}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={form.confidence}
                  onChange={(e) => setForm({ ...form, confidence: Number(e.target.value) })}
                  className="w-full"
                />
              </Field>
              <Button
                variant="primary"
                size="md"
                className="w-full"
                disabled={
                  create.isPending ||
                  !form.title.trim() ||
                  !form.trigger.trim() ||
                  !form.rule.trim() ||
                  !form.counterexample.trim()
                }
                onClick={() =>
                  create.mutate(form, {
                    onSuccess: () =>
                      setForm({ title: "", trigger: "", rule: "", counterexample: "", confidence: 60 }),
                  })
                }
              >
                {create.isPending ? "创建中…" : "创建（draft）"}
              </Button>
              {create.error ? <p className="text-[11px] text-danger">创建失败：五项都要填</p> : null}
            </div>
          </Card>
        </div>
      ) : null}

      {tab === "skills" ? (
        <Card>
          <CardHeader title="skill 清单" hint="没有 eval set 的只能停在 draft" />
          <AsyncBoundary
            isLoading={skills.isLoading}
            error={skills.error}
            isEmpty={(skills.data?.length ?? 0) === 0}
            onRetry={() => void skills.refetch()}
            emptyTitle="还没有 skill"
          >
            <ul className="divide-y divide-line">
              {skills.data?.map((skill) => (
                <li key={skill.id} className="px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[13px] font-medium">
                      {skill.name} <span className="text-[11px] text-muted">v{skill.version}</span>
                    </div>
                    <Badge tone={skill.status === "active" ? "ok" : "neutral"}>{skill.status}</Badge>
                  </div>
                  <div className="mt-1 text-[11px] text-muted">
                    {skill.description || "无描述"} · eval 集 {skill.eval_task_ids.length} 题 · 复核{" "}
                    {shortDate(skill.review_due_at)}
                  </div>
                </li>
              ))}
            </ul>
          </AsyncBoundary>
        </Card>
      ) : null}

      {tab === "harness" ? (
        <Card>
          <CardHeader title="harness 模板" hint="skill + prompt + 工具配置 + eval 集，开箱能跑才算模板" />
          <AsyncBoundary
            isLoading={harness.isLoading}
            error={harness.error}
            isEmpty={(harness.data?.length ?? 0) === 0}
            onRetry={() => void harness.refetch()}
            emptyTitle="还没有 harness 模板"
            emptyHint="模板才是真正的产品：有 eval 兜底、换环境也能跑。"
          >
            <ul className="divide-y divide-line">
              {harness.data?.map((tpl) => (
                <li key={tpl.id} className="px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[13px] font-medium">
                      {tpl.name} <span className="text-[11px] text-muted">v{tpl.version}</span>
                    </div>
                    <Badge tone={tpl.eval_task_ids.length ? "ok" : "warn"}>
                      {tpl.eval_task_ids.length ? "已验证" : "未验证"}
                    </Badge>
                  </div>
                  <div className="mt-1 text-[11px] text-muted">
                    引用 {tpl.skill_ids.length} 个 skill · eval {tpl.eval_task_ids.length} 题
                  </div>
                </li>
              ))}
            </ul>
          </AsyncBoundary>
        </Card>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-muted">{label}</span>
      {children}
    </label>
  );
}
