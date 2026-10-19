"use client";

/**
 * 设计系统原语。页面不允许自己拼"加载/空/错误"三件套，也不允许写死颜色。
 * 颜色一律走 globals.css 的 CSS 变量（--ink/--muted/--line/--brand/--ok/--warn/--danger）。
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Card({
  children,
  className = "",
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article";
}) {
  return (
    <Tag className={`rounded-card border border-line bg-surface ${className}`}>{children}</Tag>
  );
}

export function CardHeader({ title, hint, right }: { title: string; hint?: string; right?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
      </div>
      {right}
    </div>
  );
}

type Tone = "neutral" | "brand" | "ok" | "warn" | "danger";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "bg-canvas text-muted border-line",
  brand: "bg-brand/10 text-brand border-brand/20",
  ok: "bg-ok/10 text-ok border-ok/20",
  warn: "bg-warn/10 text-warn border-warn/20",
  danger: "bg-danger/10 text-danger border-danger/20",
};

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md";
};

export function Button({ variant = "ghost", size = "sm", className = "", ...rest }: ButtonProps) {
  const base =
    "inline-flex items-center justify-center rounded-md border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand";
  const variants = {
    primary: "border-brand bg-brand text-white hover:brightness-110",
    ghost: "border-line bg-surface text-ink hover:bg-canvas",
    danger: "border-danger/30 bg-danger/10 text-danger hover:bg-danger/20",
  };
  const sizes = { sm: "h-7 px-2.5 text-[12px]", md: "h-9 px-3.5 text-[13px]" };
  return <button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`} {...rest} />;
}

export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: Tone;
}) {
  const valueTone =
    tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : "text-ink";
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${valueTone}`}>{value}</div>
      {hint ? <div className="mt-1 text-[10px] text-muted">{hint}</div> : null}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-line/70" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <div className="text-sm font-medium text-ink">{title}</div>
      {hint ? <div className="max-w-md text-[12px] leading-relaxed text-muted">{hint}</div> : null}
      {action}
    </div>
  );
}

export function ErrorState({
  code,
  requestId,
  onRetry,
  hint,
}: {
  code: string;
  requestId?: string;
  onRetry?: () => void;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <div className="text-sm font-medium text-danger">出错了</div>
      <div className="text-[12px] text-muted">
        {hint ?? "请求失败，可稍后重试。"}
        {requestId ? <span className="ml-2 font-mono text-[10px]">request_id={requestId}</span> : null}
      </div>
      {onRetry ? (
        <Button onClick={onRetry} variant="ghost">
          重试
        </Button>
      ) : null}
    </div>
  );
}

export function ProgressBar({
  value,
  max,
  tone = "brand",
}: {
  value: number;
  max: number;
  tone?: "brand" | "ok" | "warn" | "danger";
}) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  const bar =
    tone === "danger" ? "bg-danger" : tone === "warn" ? "bg-warn" : tone === "ok" ? "bg-ok" : "bg-brand";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`rounded px-2.5 py-1 text-[12px] ${
            value === opt.value ? "bg-brand/10 font-medium text-brand" : "text-muted hover:text-ink"
          }`}
          type="button"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
