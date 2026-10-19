"""goals 双轨模板库。

模板的作用不是限制，是**让三问有默认答案**，把 3 分钟压缩到 40 秒
（详见 docs/PRODUCT-DESIGN-v0.2.md §2.3）。

按计划，Wave 3 先落地 3 个（A 内容引流 / G 熵减 / D 阅读），
其余 5 个仅返回 `planned`，避免半成品上线。
"""

from __future__ import annotations

from typing import Literal

from app.modules.intake.schemas import ThreeQuestions

Track = Literal["money", "growth"]
TemplateId = Literal["A", "B", "C", "D", "E", "F", "G", "H"]

# Wave 3 落地的 3 个模板
_AVAILABLE: set[str] = {"A", "G", "D"}


def _three(a: str, h: str, b: str, suggest: bool = False) -> ThreeQuestions:
    return ThreeQuestions(acceptance=a, horizon=h, budget=b, suggest_interest=suggest)


# 模板默认三问答案。字段名固定 acceptance / horizon / budget。
_TEMPLATES: dict[str, dict] = {
    "A": {
        "track": "money",
        "name": "内容引流",
        "north_star": "有效线索数",
        "guardrails": {"违规": 0, "单线索成本上限": "¥12"},
        "deliverables": {"模板": "选题 → 生产 → 发布 → 承接"},
        "defaults": _three(
            a="留下联系方式且通过初筛的私域线索 ≥ N 条",
            h="30 天内",
            b="¥6,000 含 15% 预备金",
        ),
    },
    "G": {
        "track": "growth",
        "name": "熵减",
        "north_star": "可计量的减少量",
        "guardrails": {"天生有终点": True},
        "deliverables": {"模板": "省下的钱 / 释放的 GB / 减少的维护次数"},
        "defaults": _three(
            a="完成 N 项订阅审计并取消 ≥ M 项；释放 ≥ X GB；省下 ≥ ¥Y",
            h="30 天内",
            b="0 元 + 3 小时",
        ),
    },
    "D": {
        "track": "growth",
        "name": "阅读与观影",
        "north_star": "可复用认知资产数量",
        "guardrails": {"每周 ≤2 本": True, "无卡不算完成": True},
        "deliverables": {
            "模板": "触发条件 / 判断规则 / 反例 / 置信度"
        },
        "defaults": _three(
            a="产出 1 张认知卡（触发条件/规则/反例/置信度/失效期）",
            h="14 天内",
            b="0 元 + 4 小时",
        ),
    },
}


def list_templates() -> list[dict]:
    """返回 8 个模板说明，3 个有 defaults，5 个 status=planned。"""
    out: list[dict] = []
    for tid in ("A", "B", "C", "D", "E", "F", "G", "H"):
        if tid in _TEMPLATES:
            t = _TEMPLATES[tid]
            out.append(
                {
                    "id": tid,
                    "name": t["name"],
                    "track": t["track"],
                    "status": "available",
                    "defaults": t["defaults"].model_dump(),
                    "north_star": t["north_star"],
                    "guardrails": t["guardrails"],
                    "deliverables": t["deliverables"],
                }
            )
        else:
            out.append({"id": tid, "status": "planned"})
    return out


def get_template_defaults(template_id: str) -> ThreeQuestions | None:
    """返回某模板的默认三问，模板不存在/未实现返回 None。"""
    t = _TEMPLATES.get(template_id)
    if t is None:
        return None
    return t["defaults"]


__all__ = ["list_templates", "get_template_defaults"]