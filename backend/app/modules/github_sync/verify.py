"""Webhook 验签与请求头解析。

只做无状态原语：不查库、不依赖 FastAPI 请求对象，方便单测。
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.errors import UnauthorizedError

SIGNATURE_HEADER = "x-hub-signature-256"
DELIVERY_HEADER = "x-github-delivery"
EVENT_HEADER = "x-github-event"


def compute_signature(secret: str, body: bytes) -> str:
    """GitHub 的签名格式：`sha256=<hex>`，对**原始字节**计算，不是对解析后的 JSON。"""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret:
        # 未配置密钥时一律拒绝：宁可不收，也不接受"无签名的 webhook"
        return False
    if not header:
        return False
    return hmac.compare_digest(compute_signature(secret, body), header.strip())


def require_signature(secret: str, body: bytes, header: str | None) -> None:
    if not verify_signature(secret, body, header):
        raise UnauthorizedError("webhook 签名校验失败")


def parse_delivery(delivery_id: str | None, event: str | None) -> tuple[str, str]:
    """缺 header 时拒绝，而不是用随机 id 兜底——否则重放防护形同虚设。"""
    if not delivery_id or not event:
        raise UnauthorizedError("缺少 X-GitHub-Delivery 或 X-GitHub-Event")
    return delivery_id, event


__all__ = [
    "DELIVERY_HEADER",
    "EVENT_HEADER",
    "SIGNATURE_HEADER",
    "compute_signature",
    "parse_delivery",
    "require_signature",
    "verify_signature",
]
