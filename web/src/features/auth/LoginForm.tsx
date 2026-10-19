"use client";

/**
 * 登录表单（S1-03 / S1-05）。
 *
 * 约定：
 * - access token 只进内存（setAccessToken），refresh token 走 HttpOnly Cookie，JS 不碰；
 * - 登录成功回跳 `next`（仅站内相对路径，由 login page 校验），缺省 /today；
 * - 429 限流显示真实剩余时间（details.retry_after），不写死"15 分钟"；
 * - 账号/密码错误用后端 message（"主体或密码错误"），比通用 unauthorized 文案准确。
 */

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button, Card, CardHeader } from "@/components/ui";
import { ApiError, api, setAccessToken } from "@/lib/api/client";
import { errorText, rateLimitedHint } from "@/lib/api/errorText";

export function LoginForm({ next }: { next?: string }) {
  const router = useRouter();
  const [subject, setSubject] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const body = await api.post<{ access_token: string }>("/auth/login", {
        subject: subject.trim(),
        password,
      });
      setAccessToken(body.access_token);
      router.replace(next ?? "/today");
    } catch (err) {
      setError(loginErrorText(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <Card>
      <CardHeader title="登录" hint="单用户白名单；未在白名单内的账号无法进入" />
      <form className="space-y-3 px-4 py-4" onSubmit={submit} noValidate>
        <label className="block">
          <span className="text-[11px] text-muted">账号</span>
          <input
            className="input mt-1"
            name="subject"
            autoComplete="username"
            autoFocus
            required
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-[11px] text-muted">密码</span>
          <input
            className="input mt-1"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error ? (
          <p className="text-[12px] leading-relaxed text-danger" role="alert">
            {error}
          </p>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          size="md"
          className="w-full"
          disabled={pending || !subject.trim() || !password}
        >
          {pending ? "登录中…" : "登录"}
        </Button>
      </form>
    </Card>
  );
}

function loginErrorText(err: unknown): string {
  if (!(err instanceof ApiError)) return "网络不可用，请检查连接";
  if (err.code === "rate_limited") {
    return rateLimitedHint(err.details) ?? errorText("rate_limited");
  }
  if (err.code === "validation_failed") return errorText("validation_failed");
  // http_xxx = 后端返回了非 JSON 错误（如反代 502 页面）：服务侧问题，不是用户输错
  if (err.code.startsWith("http_")) return "服务暂时不可用，请稍后再试";
  // 登录接口的 unauthorized 是"账号或密码错"，不是"登录已过期"——用后端原句
  return err.message || errorText(err.code);
}
