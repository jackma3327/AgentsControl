"use client";

/**
 * 侧边栏：1024px 以下折叠为图标栏，导航项一律来自冻结的 nav.ts。
 * 不在页面里写菜单——避免 10 个窗口各加一行造成冲突。
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_GROUPS, navByGroup } from "@/config/nav";

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 flex h-screen w-14 shrink-0 flex-col border-r border-line bg-surface px-1.5 py-4 lg:w-56 lg:px-3">
      <div className="px-1 pb-4 lg:px-2">
        <div className="hidden text-sm font-semibold tracking-tight lg:block">Agent Fleet Control</div>
        <div className="mx-auto flex h-7 w-7 items-center justify-center rounded bg-brand/10 text-[11px] font-semibold text-brand lg:hidden">
          AF
        </div>
        <div className="mt-1 hidden text-[11px] text-muted lg:block">控制面 · 任务 / 预算 / 验收</div>
      </div>

      <nav className="flex flex-1 flex-col gap-3 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group}>
            <div className="hidden px-2 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted lg:block">
              {group}
            </div>
            <ul className="flex flex-col gap-0.5">
              {navByGroup(group).map((item) => {
                const active = pathname === item.href;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      prefetch={false}
                      title={`${item.label} · ${item.question}`}
                      aria-current={active ? "page" : undefined}
                      className={`flex items-center gap-2 rounded-md px-1.5 py-1.5 text-[13px] transition-colors lg:px-2 ${
                        active ? "bg-brand/10 font-medium text-brand" : "text-ink hover:bg-canvas"
                      }`}
                    >
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-canvas text-[11px] text-muted lg:hidden">
                        {item.label.slice(0, 1)}
                      </span>
                      <span className="hidden flex-1 lg:block">{item.label}</span>
                      {item.stub ? (
                        <span className="hidden rounded bg-canvas px-1 text-[10px] text-muted lg:inline">
                          占位
                        </span>
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="hidden px-2 pt-4 text-[10px] text-muted lg:block">
        <div>v{process.env.NEXT_PUBLIC_APP_VERSION ?? "0.1.0"}</div>
        <div className="mt-1">
          <kbd className="rounded border border-line px-1">?</kbd> 快捷键
        </div>
      </div>
    </aside>
  );
}
