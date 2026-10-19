"use client";

/**
 * 8 维雷达：手绘 SVG，不引 ECharts（plan/W10.md I05 明确要求）。
 *
 * 读法：
 * - 每个模型一条多边形；虚线环是 25/50/75/100 分；
 * - 缺样本的维度画成灰色空轴，并在图例旁写"缺样本"——
 *   0 分和"没测"在视觉上必须能区分。
 */

import type { RadarDimension } from "@/lib/api/types";

const SIZE = 300;
const CENTER = SIZE / 2;
const RADIUS = 112;

const COLORS = ["var(--brand)", "var(--ok)", "var(--warn)", "var(--danger)", "var(--chart-4)"];

function point(index: number, total: number, value: number): [number, number] {
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2;
  const r = (RADIUS * Math.max(0, Math.min(100, value))) / 100;
  return [CENTER + r * Math.cos(angle), CENTER + r * Math.sin(angle)];
}

export function RadarChart({
  dimensions,
  models,
  selected,
}: {
  dimensions: RadarDimension[];
  models: string[];
  selected?: string;
}) {
  const total = dimensions.length || 8;
  const shown = selected ? models.filter((m) => m === selected) : models;
  const insufficient = dimensions.filter((d) => d.status === "insufficient").map((d) => d.label);

  return (
    <div className="flex flex-col items-center">
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="img"
        aria-label={`模型能力雷达，共 ${total} 个维度`}
      >
        {[25, 50, 75, 100].map((ring) => (
          <polygon
            key={ring}
            points={dimensions.map((_, i) => point(i, total, ring).join(",")).join(" ")}
            fill="none"
            stroke="var(--line)"
            strokeDasharray={ring === 100 ? undefined : "3 3"}
          />
        ))}

        {dimensions.map((dim, i) => {
          const [x, y] = point(i, total, 100);
          const [lx, ly] = point(i, total, 122);
          return (
            <g key={dim.dimension}>
              <line x1={CENTER} y1={CENTER} x2={x} y2={y} stroke="var(--line)" />
              <text
                x={lx}
                y={ly}
                fontSize={10}
                textAnchor={lx > CENTER + 10 ? "start" : lx < CENTER - 10 ? "end" : "middle"}
                dominantBaseline="middle"
                fill={dim.status === "insufficient" ? "var(--muted)" : "var(--ink)"}
              >
                {dim.label}
              </text>
            </g>
          );
        })}

        {shown.map((model, mi) => {
          const pts = dimensions
            .map((dim, i) => {
              const value = dim.by_model[model] ?? 0;
              return point(i, total, value).join(",");
            })
            .join(" ");
          const color = COLORS[mi % COLORS.length];
          return (
            <polygon
              key={model}
              points={pts}
              fill={color}
              fillOpacity={shown.length > 1 ? 0.06 : 0.12}
              stroke={color}
              strokeWidth={1.5}
            >
              <title>{model}</title>
            </polygon>
          );
        })}
      </svg>

      <div className="mt-1 flex flex-wrap justify-center gap-x-3 gap-y-1 text-[10px]">
        {shown.map((model, mi) => (
          <span key={model} className="inline-flex items-center gap-1 text-muted">
            <span
              className="inline-block h-1.5 w-3 rounded"
              style={{ background: COLORS[mi % COLORS.length] }}
            />
            {model}
          </span>
        ))}
      </div>
      {insufficient.length > 0 ? (
        <p className="mt-1 text-[10px] text-muted">缺样本（不按 0 计）：{insufficient.join("、")}</p>
      ) : null}
    </div>
  );
}
