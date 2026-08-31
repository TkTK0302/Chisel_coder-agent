"""安全风险等级（四级：UNKNOWN / LOW / MEDIUM / HIGH / CRITICAL）。"""
from __future__ import annotations

from enum import Enum


class SecurityRisk(str, Enum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def description(self) -> str:
        desc = {
            SecurityRisk.LOW: "Low risk - safe operation, minimal security impact",
            SecurityRisk.MEDIUM: "Medium risk - moderate impact, review recommended",
            SecurityRisk.HIGH: "High risk - significant impact, confirmation required",
            SecurityRisk.CRITICAL: "CRITICAL - catastrophic impact, forced confirmation with recovery snapshot",
            SecurityRisk.UNKNOWN: "Unknown risk - could not be determined",
        }
        return desc.get(self, "Unknown risk level")

    @property
    def emoji(self) -> str:
        return {"LOW": "✅", "MEDIUM": "⚠️", "HIGH": "🔴", "CRITICAL": "💀", "UNKNOWN": "❓"}.get(self.value, "❓")

    def is_riskier(self, other: SecurityRisk) -> bool:
        order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        return order.get(self.value, 0) >= order.get(other.value, 0) if self != SecurityRisk.UNKNOWN and other != SecurityRisk.UNKNOWN else False