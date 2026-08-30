"""确认策略（匹配 OpenHands ConfirmationPolicy 架构）。

三种策略：
  - AlwaysConfirm：全部确认
  - NeverConfirm：全部放行（不推荐）
  - ConfirmRisky：按风险阈值确认（默认 HIGH 以上才问）
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.security_risk import SecurityRisk


class ConfirmationPolicy(ABC):
    @abstractmethod
    def should_confirm(self, risk: SecurityRisk = SecurityRisk.UNKNOWN) -> bool:
        ...


class AlwaysConfirm(ConfirmationPolicy):
    def should_confirm(self, risk: SecurityRisk = SecurityRisk.UNKNOWN) -> bool:
        return True


class NeverConfirm(ConfirmationPolicy):
    def should_confirm(self, risk: SecurityRisk = SecurityRisk.UNKNOWN) -> bool:
        return False


class ConfirmRisky(ConfirmationPolicy):
    def __init__(self, threshold: SecurityRisk = SecurityRisk.HIGH, confirm_unknown: bool = True):
        self.threshold = threshold
        self.confirm_unknown = confirm_unknown

    def should_confirm(self, risk: SecurityRisk = SecurityRisk.UNKNOWN) -> bool:
        if risk == SecurityRisk.UNKNOWN:
            return self.confirm_unknown
        return risk.is_riskier(self.threshold)