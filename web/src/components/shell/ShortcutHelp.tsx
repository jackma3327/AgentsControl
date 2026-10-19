"use client";

/**
 * 全局快捷键面板：任意页按 `?` 呼出，Esc 关闭。
 *
 * 只在 (console) 布局挂载一次，页面自己不再重复实现键盘监听。
 * 焦点在输入框/文本域/下拉框或 contenteditable 时不触发——否则用户打不出问号。
 */

import { useEffect, useState } from "react";

const SHORTCUTS: readonly { keys: readonly string[]; label: string; scope?: string }[] = [
  { keys: ["J", "K"], label: "切换决策卡", scope: "今日" },
  { keys: ["Enter"], label: "通过当前决策", scope: "今日" },
  { keys: ["X"], label: "拒绝当前决策", scope: "今日" },
  { keys: ["Cmd/Ctrl", "Enter"], label: "提交记录", scope: "收件箱" },
  { keys: ["?"], label: "打开 / 关闭这个面板" },
  { keys: ["Esc"], label: "关闭面板" },
];

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export function ShortcutHelp() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target)) return;
      if (event.key === "?" || event.key === "？") {
        event.preventDefault();
        setOpen((value) => !value);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="presentation"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcut-help-title"
        className="w-full max-w-md rounded-card border border-line bg-surface p-4 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="shortcut-help-title" className="text-sm font-semibold">
              快捷键
            </h2>
            <p className="mt-0.5 text-[11px] text-muted">焦点在输入框里时不触发，先点空白处。</p>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded border border-line px-1.5 py-0.5 text-[11px] text-muted hover:bg-canvas"
            aria-label="关闭快捷键面板"
          >
            Esc
          </button>
        </div>

        <dl className="mt-3 divide-y divide-line">
          {SHORTCUTS.map((item) => (
            <div key={item.label} className="flex items-center justify-between gap-3 py-2">
              <dt className="text-[12px]">
                {item.label}
                {item.scope ? <span className="ml-1 text-[10px] text-muted">· {item.scope}</span> : null}
              </dt>
              <dd className="flex shrink-0 gap-1">
                {item.keys.map((key) => (
                  <kbd
                    key={key}
                    className="rounded border border-line bg-canvas px-1.5 py-0.5 font-mono text-[10px] text-muted"
                  >
                    {key}
                  </kbd>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
