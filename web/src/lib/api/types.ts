/**
 * 手写 API 契约（只覆盖 UI 用到的字段）。
 *
 * 为什么手写而不是全部由 OpenAPI 生成：
 * - 生成物在 `npm run gen:api` 时产出，CI 没有运行中的后端；
 * - 未合并模块的端点（tasks/devices）也必须先能画出界面。
 * 因此这里只声明"UI 真正读的字段"，并在每个未合并端点旁标 `TODO(sync)`。
 * 后端字段改名时，W10 的 typecheck 会失败——这是故意的。
 */

/** 收件箱条目 —— W02 已合并 */
export interface IntakeItem {
  id: string;
  text: string;
  source: string;
  status: "raw" | "structured" | "promoted" | "archived_interest";
  created_at: string;
  structured_at: string | null;
  structured: ThreeQuestions | null;
  structure_cost_usd: number | null;
  promoted_goal_id: string | null;
}

export interface ThreeQuestions {
  acceptance: string | null;
  horizon: string | null;
  budget: string | null;
  suggest_interest: boolean;
}

export interface IntakeList {
  items: IntakeItem[];
  next_cursor: string | null;
}

export interface StructureResult {
  item_id: string;
  structured: ThreeQuestions;
  model: string;
  cost_usd: number;
}

/** 目标 —— W02 已合并 */
export type GoalTrack = "money" | "growth";
export type GoalStatus = "draft" | "active" | "achieved" | "frozen" | "degraded";

export interface Goal {
  id: string;
  code: string;
  track: GoalTrack;
  template: string;
  title: string;
  acceptance: string;
  horizon: string | null;
  horizon_at: string | null;
  budget_money: number | null;
  budget_hours: number | null;
  attention_minutes_per_day: number | null;
  north_star: string | null;
  guardrails: Record<string, unknown> | null;
  deliverables: Record<string, unknown> | null;
  status: GoalStatus;
  intake_item_id: string | null;
  created_at: string;
  stop_loss_triggered_at: string | null;
}

export interface GoalCreate {
  track: GoalTrack;
  template?: string;
  title: string;
  acceptance: string;
  horizon?: string | null;
  budget_money?: number | null;
  budget_hours?: number | null;
  attention_minutes_per_day?: number | null;
  intake_item_id?: string | null;
}

/** 模型雷达 —— W08（本会话新增，PR #11） */
export type DimensionStatus = "ok" | "insufficient";

export interface RadarDimension {
  dimension: string;
  label: string;
  status: DimensionStatus;
  score: number | null;
  best_model: string | null;
  by_model: Record<string, number>;
  cost_usd: number;
  p50_latency_ms: number | null;
  samples: number;
}

export interface Radar {
  week: string;
  models: string[];
  dimensions: RadarDimension[];
  generated_at: string;
}

export interface CeilingDimension {
  dimension: string;
  label: string;
  best_score: number | null;
  status: "ok" | "risky" | "blocked" | "insufficient";
  advice: string;
}

export interface Ceiling {
  week: string;
  dimensions: CeilingDimension[];
  blocked: string[];
  risky: string[];
}

export interface Skill {
  id: string;
  name: string;
  version: string;
  status: "draft" | "active" | "paused" | "stale" | "retired";
  description: string;
  trigger: string;
  criteria: string;
  counterexample: string;
  eval_task_ids: string[];
  rollout_pct: number;
  review_due_at: string | null;
  created_at: string;
}

export interface HarnessTemplate {
  id: string;
  name: string;
  version: string;
  skill_ids: string[];
  config: Record<string, unknown>;
  eval_task_ids: string[];
  created_at: string;
}

export interface CognitionCard {
  id: string;
  title: string;
  trigger: string;
  rule: string;
  counterexample: string;
  confidence: number;
  expires_at: string | null;
  reuse_count: number;
  status: "draft" | "active" | "stale";
  last_reused_at: string | null;
  created_at: string;
}

export interface EvalRun {
  id: string;
  week: string;
  status: "queued" | "running" | "done" | "aborted" | "error";
  models: string[];
  cost_cap_usd: number;
  cost_used_usd: number;
  task_count: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface DriftAlert {
  id: string;
  model: string;
  dimension: string;
  week: string;
  prev_week: string;
  prev_score: number;
  curr_score: number;
  delta_pct: number;
  status: "open" | "ack" | "ignored";
  note: string | null;
  created_at: string;
}

/** 账本 —— W07 已合并 */
export interface AttentionStats {
  period: string;
  minutes: number;
  outcomes: number | null;
  min_per_outcome: number | null;
}

export interface LedgerSummary {
  period: string;
  currency: string;
  categories: Record<string, number>;
  total: number;
  attention: AttentionStats;
}

/** 闸门 —— W07 已合并 */
export interface Approval {
  id: string;
  level: "L0" | "L1" | "L2" | "L3";
  title: string;
  payload: Record<string, unknown> | null;
  status: "pending" | "approved" | "rejected" | "expired" | string;
  visible_at: string;
  created_at: string;
  decision: string | null;
  held_reason: string | null;
}

export interface ApprovalQueue {
  items: Approval[];
  count: number;
}

export interface Bandwidth {
  config: {
    daily_decision_limit: number;
    duration_window_days: number;
    duration_rise_pct: number;
    dispatch_cut_pct: number;
    veto_floor_pct: number;
    veto_ceiling_pct: number;
    review_sample: number;
    night_start_hour: number;
    night_end_hour: number;
  };
  input: {
    decisions_today: number;
    daily_avg_minutes: number[];
    veto_rate_recent: number;
    veto_rate_baseline: number;
    now_hour: number;
  };
  verdict: {
    defer_non_critical: boolean;
    dispatch_factor: number;
    fatigue_suspected: boolean;
    review_sample: number;
    night_hold_l0: boolean;
    reasons: string[];
  };
}

/** 任务 —— TODO(sync): W03 合并后替换为生成类型 */
export interface Task {
  id: string;
  title: string;
  state: string;
  device_id: string | null;
  goal_id: string | null;
  created_at: string;
  updated_at: string;
}

/** 设备 —— W04 已合并，字段对齐 `GET /devices/` 的 DeviceOut */
export type DeviceStatus = "online" | "suspect" | "offline" | "revoked";

export interface Device {
  node_id: string;
  trust: "managed" | "third_party";
  purpose: string;
  squad: string | null;
  tolerance: number;
  caps: string[];
  status: DeviceStatus;
  agent_version: string | null;
  last_seen_at: string | null;
  missed_heartbeats: number;
  last_event_seq: number;
  woke_at: string | null;
  created_at: string | null;
  revoked_at: string | null;
}
