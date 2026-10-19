"use client";

/**
 * 统一的加载 / 错误 / 空态边界。页面只负责"有数据时画什么"。
 * 错误文案按 error.code 映射，4xx 不提示重试（重试也没用），5xx 提供重试。
 */

import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import { errorText } from "@/lib/api/errorText";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";

export function AsyncBoundary({
  isLoading,
  error,
  isEmpty = false,
  onRetry,
  emptyTitle = "还没有数据",
  emptyHint,
  emptyAction,
  loadingRows = 3,
  children,
}: {
  isLoading: boolean;
  error: unknown;
  isEmpty?: boolean;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyHint?: string;
  emptyAction?: ReactNode;
  loadingRows?: number;
  children: ReactNode;
}) {
  if (isLoading) {
    return (
      <div className="px-4 py-5">
        <Skeleton rows={loadingRows} />
      </div>
    );
  }

  if (error) {
    if (error instanceof ApiError) {
      // 501 = 后端模块还没合并：这不是"故障"，不要给用户红色报错
      const notImplemented = error.code === "not_implemented";
      return (
        <ErrorState
          code={error.code}
          requestId={error.requestId}
          hint={notImplemented ? "该模块尚未接入（等对应窗口合并后自动可用）" : errorText(error.code, error.message)}
          onRetry={error.retryable ? onRetry : undefined}
        />
      );
    }
    return <ErrorState code="network_error" hint="网络不可用，请检查连接" onRetry={onRetry} />;
  }

  if (isEmpty) {
    return <EmptyState title={emptyTitle} hint={emptyHint} action={emptyAction} />;
  }

  return <>{children}</>;
}
