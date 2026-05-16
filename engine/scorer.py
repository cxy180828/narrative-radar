"""
Multi-dimensional scoring engine for momentum signals.
"""

from typing import Optional
from infra.logger import get_logger


class SignalScorer:
    def __init__(self, config: dict):
        self._logger = get_logger()
        s = config.get("scoring", {})
        self._gain_weight = s.get("gain_weight", 2.5)
        self._gain_max = s.get("gain_max", 35)
        self._streak_base = s.get("streak_base", 10)
        self._streak_per_round = s.get("streak_per_round", 5)
        self._streak_max = s.get("streak_max", 25)
        self._repeat_weight = s.get("repeat_signal_weight", 5)
        self._repeat_max = s.get("repeat_signal_max", 15)
        self._vol_up_bonus = s.get("volume_up_bonus", 10)
        self._chg_1h_divisor = s.get("chg_1h_divisor", 3)
        self._chg_1h_max = s.get("chg_1h_max", 8)
        self._liq_ratio_mult = s.get("liq_ratio_multiplier", 40)
        self._liq_ratio_max = s.get("liq_ratio_max", 4)
        self._consecutive_up = config.get("momentum", {}).get("consecutive_up", 3)

    def score(self, token: dict, pct_gain: float, streak_rounds: int, signal_count: int,
              vol_up: bool, category: str, desc_info: Optional[dict] = None,
              desc_grade: Optional[str] = None, momentum_decay: float = 1.0,
              ai_result: Optional[dict] = None) -> int:
        score = 0.0
        score += min(self._gain_max, pct_gain * self._gain_weight)
        if streak_rounds >= self._consecutive_up:
            extra = streak_rounds - self._consecutive_up
            score += min(self._streak_max, self._streak_base + extra * self._streak_per_round)
        if signal_count > 1:
            score += min(self._repeat_max, (signal_count - 1) * self._repeat_weight)
        if vol_up:
            score += self._vol_up_bonus
        chg_1h = token.get("chg_1h", 0) or 0
        if chg_1h > 0:
            score += min(self._chg_1h_max, chg_1h / self._chg_1h_divisor)
        mc = token.get("mc", 0) or 0
        liq = token.get("liq", 0) or 0
        if mc > 0:
            score += min(self._liq_ratio_max, (liq / mc) * self._liq_ratio_mult)
        age_h = token.get("age_h", 999) or 999
        if age_h <= 6:
            score += 3
        elif age_h <= 24:
            score += 1
        if category in ("musk_trump",):
            score += 3
        elif category in ("binance_cz",):
            score += 3
        elif category in ("celebrity_viral",):
            score += 2
        if desc_info and (desc_info.get("twitter") or desc_info.get("telegram") or desc_info.get("website")):
            score += 2
        if ai_result and ai_result.get("category") not in ("noise", None):
            score += 3
        if desc_grade == "A":
            score += 3
        elif desc_grade == "B":
            score += 1
        if momentum_decay < 0.8:
            score -= (1.0 - momentum_decay) * 10
        sm = token.get("sm", 0) or 0
        if sm >= 3:
            score += min(5, sm)
        return max(0, min(100, int(round(score))))

    def get_push_level(self, score: int, config: dict) -> str:
        push_cfg = config.get("push", {})
        if score >= push_cfg.get("high_score_threshold", 75):
            return "high"
        elif score >= push_cfg.get("medium_score_threshold", 50):
            return "medium"
        return "low"
