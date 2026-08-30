"""安全风险等级（匹配 OpenHands SecurityRisk 架构）。"""
from __future__ import annotations

from enum import Enum


class SecurityRisk(str, Enum):
    """安全风险等级，基于 OpenHands SecurityRisk 设计。"""
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def description(self) -> str:
        desc = {
            SecurityRisk.LOW: "Low risk - safe operation, minimal security impact",
            SecurityRisk.MEDIUM: "Medium risk - moderate impact, review recommended",
            SecurityRisk.HIGH: "High risk - significant impact, confirmation required",
            SecurityRisk.UNKNOWN: "Unknown risk - could not be determined",
        }
        return desc.get(self, "Unknown risk level")

    def is_riskier(self, other: SecurityRisk) -> bool:
        """self 是否比 other 更危险。"""
        order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        return order.get(self.value, 0) >= order.get(other.value, 0) if self != SecurityRisk.UNKNOWN and other != SecurityRisk.UNKNOWN else False