"use client";

/**
 * 项目视图：横轴时间、纵轴 Agent 的甘特图。
 *
 * 这不是"看它在跑"，是发现三件事：
 *   撞车（同文件被两个 Agent 改）/ 阻塞（等依赖或审批超时）/ 偏离（改了无关文件）。
 *
 * 数据来源：W06 的 execution_sessions 尚未合并，当前用明确标注的示例数据；
 * 三种标记的判定逻辑与颜色语义已固定，接入后只换数据源，不改视图。
 */

import { useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Badge, Card, CardHeader } from "@/components/ui";

type Kind = "normal" | "collision" | "blocked" | "deviation";

interface GanttData extends Record<string, unknown> {
  label: string;
  issue: string;
  kind: Kind;
  hours: number;
  note: string;
}

type GanttNode = Node<GanttData, "gantt">;

const KIND_STYLE: Record<Kind, { bg: string; border: string; text: string; label: string }> = {
  normal: { bg: "var(--brand)", border: "var(--brand)", text: "var(--on-strong)", label: "正常" },
  collision: { bg: "var(--danger)", border: "var(--danger)", text: "var(--on-strong)", label: "撞车" },
  blocked: { bg: "var(--warn)", border: "var(--warn)", text: "var(--on-strong)", label: "阻塞" },
  deviation: { bg: "var(--caution)", border: "var(--caution)", text: "var(--caution-ink)", label: "偏离" },
};

const LANES = ["Agent A", "Agent B", "Agent C"];
const PX_PER_HOUR = 56;

// 示例数据（09:00 起算）：接入 W06 后由 sessions API 生成
const DEMO: GanttData[] = [
  { label: "W01 身份内核", issue: "#1", kind: "normal", hours: 3, note: "正常交付" },
  { label: "W03 任务队列", issue: "#8", kind: "normal", hours: 2.5, note: "正常交付" },
  { label: "W02 目标面", issue: "#2", kind: "collision", hours: 2, note: "与 W02 同时改了 goals/service.py" },
  { label: "W02 目标面", issue: "#2", kind: "collision", hours: 1.5, note: "同上（另一条分支）" },
  { label: "W07 账本", issue: "#2", kind: "blocked", hours: 3, note: "等迁移合并头，等了 2h10m" },
  { label: "W05 Go Agent", issue: "#7", kind: "deviation", hours: 2, note: "改了 deploy/ 下的无关文件" },
];

function GanttBar({ data }: NodeProps<GanttNode>) {
  const style = KIND_STYLE[data.kind];
  return (
    <div
      className="flex h-7 items-center gap-1 overflow-hidden rounded px-2 text-[10px] shadow-sm"
      style={{ background: style.bg, border: `1px solid ${style.border}`, color: style.text, width: data.hours * PX_PER_HOUR }}
      title={`${data.label} ${data.issue} · ${data.note}`}
    >
      <span className="truncate font-medium">{data.label}</span>
      <span className="shrink-0 opacity-70">{data.issue}</span>
    </div>
  );
}

const nodeTypes: NodeTypes = { gantt: GanttBar };

export function ProjectsView() {
  const nodes = useMemo<GanttNode[]>(
    () =>
      DEMO.map((item, index) => {
        const lane = index % LANES.length;
        const startHour = 9 + Math.floor(index / LANES.length) * 2 + (index % 2);
        return {
          id: `demo-${index}`,
          type: "gantt",
          position: { x: (startHour - 9) * PX_PER_HOUR, y: lane * 64 },
          data: item,
          draggable: false,
          selectable: false,
        };
      }),
    [],
  );

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">项目视图</h1>
          <p className="mt-1 text-[12px] text-muted">
            横轴时间、纵轴 Agent。要发现的是撞车、阻塞、偏离，不是"看着热闹"。
          </p>
        </div>
        <Badge tone="warn">示例数据 · W06 接入后替换</Badge>
      </header>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.35fr_0.65fr]">
        <Card className="overflow-hidden">
          <CardHeader title="甘特" hint="每根条 = 一个 Issue 的占用区间" />
          <div className="flex">
            <div className="w-16 shrink-0 border-r border-line pt-6">
              {LANES.map((lane) => (
                <div key={lane} className="flex h-16 items-center justify-end pr-2 text-[10px] text-muted">
                  {lane}
                </div>
              ))}
            </div>
            <div className="h-[260px] min-w-0 flex-1">
              <div className="ml-1 mt-2 flex gap-14 text-[10px] text-muted">
                {["09:00", "11:00", "13:00", "15:00", "17:00"].map((t) => (
                  <span key={t}>{t}</span>
                ))}
              </div>
              <div className="h-[220px] w-full">
                <ReactFlow
                  nodes={nodes}
                  edges={[]}
                  nodeTypes={nodeTypes}
                  onlyRenderVisibleElements
                  fitView
                  proOptions={{ hideAttribution: true }}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  panOnScroll
                  zoomOnDoubleClick={false}
                >
                  <Background gap={16} size={1} color="var(--line)" />
                  <Controls showInteractive={false} />
                </ReactFlow>
              </div>
            </div>
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="三种信号" hint="颜色只表达一件事，避免误读" />
            <ul className="space-y-3 p-4 text-[12px]">
              <li>
                <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--danger)" }} />
                <span className="font-medium">撞车</span>
                <p className="mt-1 text-[11px] text-muted">同一文件被两个 Agent 改。要做的不是"协调"，是改边界表。</p>
              </li>
              <li>
                <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--warn)" }} />
                <span className="font-medium">阻塞</span>
                <p className="mt-1 text-[11px] text-muted">等依赖或等审批超时。条上标红并显示等了多久。</p>
              </li>
              <li>
                <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--caution)" }} />
                <span className="font-medium">偏离</span>
                <p className="mt-1 text-[11px] text-muted">改了 Issue 之外的无关文件。通常是 Agent 在"顺手优化"。</p>
              </li>
            </ul>
          </Card>

          <Card className="p-4">
            <div className="text-[12px] font-medium">为什么现在只有示例</div>
            <p className="mt-2 text-[11px] leading-relaxed text-muted">
              会话数据来自 W06 的 Worker/Orca 会话表，任务占用区间来自 W03。两个模块尚未合并，
              这里先固定视图与判定规则，接入时只替换 <span className="font-mono">DEMO</span> 数据源。
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}
