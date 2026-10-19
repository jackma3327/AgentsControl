/** 展示层格式化。所有金额默认人民币，时间按本地时区。 */

export function yuan(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `¥${value.toFixed(2)}`;
}

export function hours(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value} h`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

export function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  const diff = new Date(iso).getTime() - Date.now();
  return Math.ceil(diff / 86_400_000);
}

export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const h = Math.round(minutes / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.round(h / 24)} 天前`;
}

/** 分类中文名（与后端 ledger category 对齐） */
export const CATEGORY_LABEL: Record<string, string> = {
  subscription: "订阅",
  tools: "工具",
  device: "设备",
  rework: "返工",
  qc: "质检",
  labor: "人力",
};
