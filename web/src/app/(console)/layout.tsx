import type { ReactNode } from "react";

import { ShortcutHelp } from "@/components/shell/ShortcutHelp";
import { Sidebar } from "@/components/shell/Sidebar";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-screen">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-x-hidden px-8 py-6">{children}</main>
      <ShortcutHelp />
    </div>
  );
}
