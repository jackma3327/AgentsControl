"""指令下发白名单（W04-I06）。

三条不可退让的语义：
1. 只支持 collect_snapshot / drain / restart_agent / run_task / collect_logs 五种；
2. **永不支持任意 shell** —— 本文件是唯一入口，任何"顺手加一种"都必须先改这里并过评审；
3. 中风险指令（restart_agent / run_task）必须带 approved_by（控制面二次确认）；
   trust=third_party 的设备一律 403，不接收任何指令。

设备在 NAT 后面只能主动拉：长轮询 GET /devices/{id}/commands?wait=25，超时 204，不用 WebSocket。
"""

from __future__ import annotations

from typing import Final, Literal

#: kind → 风险级别。白名单之外一律 422。
COMMAND_KINDS: Final[dict[str, str]] = {
    "collect_snapshot": "low",
    "drain": "low",
    "restart_agent": "medium",
    "run_task": "medium",
    "collect_logs": "low",
}

#: 中风险指令需要 approved_by
MEDIUM_RISK_KINDS: Final[frozenset[str]] = frozenset(
    k for k, v in COMMAND_KINDS.items() if v == "medium"
)

CommandKind = Literal["collect_snapshot", "drain", "restart_agent", "run_task", "collect_logs"]


def is_allowed(kind: str) -> bool:
    return kind in COMMAND_KINDS


def risk_of(kind: str) -> str:
    return COMMAND_KINDS.get(kind, "unknown")
