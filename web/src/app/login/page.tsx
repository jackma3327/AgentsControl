import type { Metadata } from "next";

import { LoginForm } from "@/features/auth/LoginForm";

export const metadata: Metadata = {
  title: "登录 · Agent Fleet Control",
};

/**
 * 登录页（S1-03）：刻意放在 `(console)` 之外——不挂侧边栏、不过 ConsoleLayout。
 * 未登录用户落到这里时，看到的应该是一张干净的登录卡，而不是"半边控制台 + 登录框"。
 *
 * `searchParams` 在 Next 15 是 Promise，需 await；`next` 只接受站内相对路径，
 * 防止 `/login?next=https://evil.com` 被当成开放重定向用。
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams?: Promise<{ next?: string | string[] }>;
}) {
  const params = (await searchParams) ?? {};
  const raw = Array.isArray(params.next) ? params.next[0] : params.next;
  const next = raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : undefined;

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto flex h-9 w-9 items-center justify-center rounded bg-brand/10 text-[13px] font-semibold text-brand">
            AF
          </div>
          <div className="mt-2 text-sm font-semibold tracking-tight">Agent Fleet Control</div>
          <p className="mt-1 text-[12px] text-muted">控制面 · 任务 / 预算 / 验收</p>
        </div>
        <LoginForm next={next} />
      </div>
    </main>
  );
}
