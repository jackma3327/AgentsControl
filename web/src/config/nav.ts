/**
 * 导航注册表 —— **冻结文件（FROZEN）**。
 *
 * 骨架期一次性把产品设计的完整信息架构（8 个业务页 + 2 个拓扑视图）登记完，
 * 之后任何窗口都不需要改这个文件：新增页面属于产品决策，走 shared-registry 变更请求。
 * 这样避免了"每个窗口在自己 PR 里加一行菜单"造成的必然冲突。
 */

export type NavGroup = "指挥" | "执行" | "资产" | "视图";

export interface NavItem {
  href: string;
  label: string;
  /** 只回答一个问题（来自 PRODUCT-DESIGN-v0.2 §6） */
  question: string;
  group: NavGroup;
  /** 规划归属的窗口（用于文档一致性检查，不参与运行时逻辑） */
  owner: string;
  /** 是否仍是占位页（false = 已实现；2026-10-19 逐页核对后全为 false） */
  stub: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/today", label: "今日", question: "今天有什么事非我不可？", group: "指挥", owner: "W10", stub: false },
  { href: "/inbox", label: "收件箱", question: "我刚想到什么来着？", group: "指挥", owner: "W10", stub: false },
  { href: "/goals", label: "目标", question: "这些事值不值得继续？", group: "指挥", owner: "W10", stub: false },
  { href: "/execution", label: "执行", question: "活干到哪了，卡在哪？", group: "执行", owner: "W10", stub: false },
  { href: "/team", label: "团队", question: "谁在干，谁该走？", group: "执行", owner: "W10", stub: false },
  { href: "/arsenal", label: "兵工厂", question: "现在能用什么，贵不贵？", group: "资产", owner: "W10", stub: false },
  { href: "/assets", label: "资产", question: "我变强了吗？", group: "资产", owner: "W10", stub: false },
  { href: "/ledger", label: "账本", question: "值不值？", group: "资产", owner: "W10", stub: false },
  { href: "/overview", label: "总视图", question: "所有项目现在什么状态？", group: "视图", owner: "W10", stub: false },
  { href: "/projects", label: "项目视图", question: "谁和谁撞车了？", group: "视图", owner: "W10", stub: false },
] as const;

export const NAV_GROUPS: readonly NavGroup[] = ["指挥", "执行", "资产", "视图"];

export function navByGroup(group: NavGroup): readonly NavItem[] {
  return NAV_ITEMS.filter((item) => item.group === group);
}
