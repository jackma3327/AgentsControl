"use client";

/**
 * TanStack Query hooks —— 页面不直接 fetch，也不自己管加载/失效。
 *
 * 约定：
 * - queryKey 统一在 `qk` 里，避免各页拼字符串导致缓存击穿；
 * - 变更成功后只失效受影响的 key，不做"全量 invalidate"；
 * - 4xx 不重试、5xx 重试 ≤3 次已在 providers.tsx 统一，这里不重复配置。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type {
  ApprovalQueue,
  Bandwidth,
  Ceiling,
  CognitionCard,
  DriftAlert,
  EvalRun,
  Goal,
  GoalCreate,
  HarnessTemplate,
  IntakeItem,
  IntakeList,
  LedgerSummary,
  Radar,
  Skill,
  StructureResult,
  Task,
} from "@/lib/api/types";

export const qk = {
  intake: (status?: string) => ["intake", status ?? "all"] as const,
  goals: ["goals"] as const,
  radar: (week?: string) => ["radar", week ?? "current"] as const,
  ceiling: (week?: string) => ["ceiling", week ?? "current"] as const,
  skills: ["skills"] as const,
  cards: ["cards"] as const,
  harness: ["harness"] as const,
  drift: ["drift"] as const,
  runs: ["runs"] as const,
  ledger: (period?: string) => ["ledger", period ?? "current"] as const,
  approvals: ["approvals"] as const,
  bandwidth: ["bandwidth"] as const,
  tasks: ["tasks"] as const,
};

// ------------------------------------------------------------------ 收件箱
export function useIntakeItems(status?: "raw" | "structured" | "archived_interest") {
  const qs = status ? `?status=${status}&limit=50` : "?limit=50";
  return useQuery({
    queryKey: qk.intake(status),
    queryFn: () => api.get<IntakeList>(`/intake/items${qs}`),
  });
}

export function useCreateIntake() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.post<IntakeItem>("/intake/items", { text, source: "web" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["intake"] });
    },
  });
}

export function useStructureIntake() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) =>
      api.post<StructureResult>(`/intake/items/${itemId}/structure`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["intake"] });
    },
  });
}

// -------------------------------------------------------------------- 目标
export function useGoals() {
  return useQuery({
    queryKey: qk.goals,
    queryFn: () => api.get<Goal[]>("/goals/?limit=100"),
  });
}

export function useCreateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: GoalCreate) => api.post<Goal>("/goals/", payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.goals });
    },
  });
}

export function usePatchGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: string; status?: Goal["status"] }) =>
      api.patch<Goal>(`/goals/${id}`, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.goals });
    },
  });
}

// ------------------------------------------------------------------ 资产面
export function useRadar(week?: string) {
  return useQuery({
    queryKey: qk.radar(week),
    queryFn: () => api.get<Radar>(`/evals/radar${week ? `?week=${week}` : ""}`),
  });
}

export function useCeiling(week?: string) {
  return useQuery({
    queryKey: qk.ceiling(week),
    queryFn: () => api.get<Ceiling>(`/evals/ceiling${week ? `?week=${week}` : ""}`),
  });
}

export function useRunEvals() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (models: string[]) =>
      api.post<EvalRun>("/evals/runs", { models, cost_cap_usd: 20 }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.runs });
      void qc.invalidateQueries({ queryKey: ["radar"] });
    },
  });
}

export function useSkills() {
  return useQuery({ queryKey: qk.skills, queryFn: () => api.get<Skill[]>("/evals/skills") });
}

export function useHarness() {
  return useQuery({
    queryKey: qk.harness,
    queryFn: () => api.get<HarnessTemplate[]>("/evals/harness"),
  });
}

export function useCards() {
  return useQuery({ queryKey: qk.cards, queryFn: () => api.get<CognitionCard[]>("/evals/cards") });
}

export function useCreateCard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      title: string;
      trigger: string;
      rule: string;
      counterexample: string;
      confidence: number;
    }) => api.post<CognitionCard>("/evals/cards", payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.cards });
    },
  });
}

export function useReuseCard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cardId: string) =>
      api.post<{ card: CognitionCard; completed: boolean }>(`/evals/cards/${cardId}/reuse`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.cards });
    },
  });
}

export function useDrift() {
  return useQuery({ queryKey: qk.drift, queryFn: () => api.get<DriftAlert[]>("/evals/drift") });
}

// -------------------------------------------------------------------- 账本
export function useLedger(period?: string) {
  return useQuery({
    queryKey: qk.ledger(period),
    queryFn: () => api.get<LedgerSummary>(`/ledger/summary${period ? `?period=${period}` : ""}`),
  });
}

// ---------------------------------------------------------------- 闸门/带宽
export function useApprovalQueue() {
  return useQuery({
    queryKey: qk.approvals,
    queryFn: () => api.get<ApprovalQueue>("/approvals/queue?limit=8"),
  });
}

export function useBandwidth() {
  return useQuery({
    queryKey: qk.bandwidth,
    queryFn: () => api.get<Bandwidth>("/approvals/bandwidth"),
  });
}

export function useDecideApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      decision,
      reason,
    }: {
      id: string;
      decision: "approved" | "rejected";
      reason?: string;
    }) => api.post<unknown>(`/approvals/${id}/decide`, { decision, reason, minutes: 1 }),
    // 乐观更新：先把卡从队列里拿掉，失败再放回（决策是高频操作，等一次往返会打断节奏）
    onMutate: async ({ id }) => {
      await qc.cancelQueries({ queryKey: qk.approvals });
      const previous = qc.getQueryData<ApprovalQueue>(qk.approvals);
      if (previous) {
        qc.setQueryData<ApprovalQueue>(qk.approvals, {
          items: previous.items.filter((item) => item.id !== id),
          count: Math.max(0, previous.count - 1),
        });
      }
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) qc.setQueryData(qk.approvals, context.previous);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.approvals });
      void qc.invalidateQueries({ queryKey: qk.bandwidth });
      void qc.invalidateQueries({ queryKey: ["ledger"] });
    },
  });
}

// -------------------------------------------------------- 未合并模块（501）
export function useTasks() {
  return useQuery({
    queryKey: qk.tasks,
    queryFn: () => api.get<{ items: Task[]; count: number }>("/tasks/?limit=100"),
    retry: false,
  });
}

// --------------------------------------------------- 单目标预算（账本只读契约）
export function useBudgetStatus(goalId: string | null) {
  return useQuery({
    queryKey: ["budget", goalId],
    queryFn: () =>
      api.get<{
        goal_id: string;
        budget: number | null;
        spent: number;
        remaining: number | null;
        frozen: boolean;
      }>(`/ledger/budget-status?goal_id=${goalId}`),
    enabled: Boolean(goalId),
    retry: false,
  });
}
