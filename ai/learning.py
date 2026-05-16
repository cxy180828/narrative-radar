"""
AI-powered false positive analysis and parameter self-tuning.
"""

import time
from typing import Optional, List

from ai.client import AIClient
from storage.database import Database
from infra.logger import get_logger

SYSTEM_PROMPT = """你是一名链上信号质量分析师。分析误报信号以找出共同模式并建议过滤器调整。

要求：
1. 识别误报的共同特征
2. 建议具体的过滤规则调整（附带阈值数值）
3. 如有必要建议新的排除关键词
4. 所有建议必须可操作

只返回JSON格式。用中文填写summary和common_patterns。"""

USER_PROMPT_TEMPLATE = """以下{count}个代币信号被标记为误报：

{false_positives_text}

当前过滤参数：
- 最小市值：${min_mc:,.0f}
- 最小流动性：${min_liq:,.0f}
- 最小流动性/市值比：{min_liq_ratio:.2f}
- 最大卖出税：{max_sell_tax:.0%}
- 最小存活时间：{min_age_min}分钟

分析并返回JSON：
{{
    "common_patterns": ["模式1", "模式2"],
    "suggested_adjustments": {{
        "min_market_cap": 建议值或null,
        "min_liquidity": 建议值或null,
        "min_liq_mc_ratio": 建议值或null,
        "min_age_minutes": 建议值或null
    }},
    "suggested_blacklist_keywords": ["词1", "词2"],
    "confidence": 0.0到1.0,
    "summary": "一句话总结主要问题"
}}"""


class FalsePositiveLearning:
    """Analyzes false positives to improve filtering."""

    def __init__(self, ai_client: AIClient, db: Database, config: dict):
        self._ai = ai_client
        self._db = db
        self._logger = get_logger()
        self._config = config
        self._enabled = config.get("ai", {}).get("false_positive_learning", False)
        self._min_fps_for_analysis = 10
        self._last_analyzed_count = 0

    @property
    def enabled(self) -> bool:
        return self._enabled and self._ai.enabled

    def should_run(self) -> bool:
        if not self.enabled:
            return False
        current_count = self._db.get_false_positive_count()
        return current_count >= self._last_analyzed_count + self._min_fps_for_analysis

    def analyze(self) -> Optional[dict]:
        if not self.enabled:
            return None
        fps = self._db.get_recent_false_positives(limit=20)
        if len(fps) < self._min_fps_for_analysis:
            return None
        self._last_analyzed_count = self._db.get_false_positive_count()
        fp_lines = []
        for fp in fps:
            line = (
                f"- {fp.get('name', '?')} ({fp.get('symbol', '?')}) "
                f"[{fp.get('chain', '?')}] reason: {fp.get('reason', 'unknown')}"
            )
            fp_lines.append(line)
        fp_text = "\n".join(fp_lines)
        thresholds = self._config.get("thresholds", {})
        prompt = USER_PROMPT_TEMPLATE.format(
            count=len(fps),
            false_positives_text=fp_text,
            min_mc=thresholds.get("min_market_cap", 1000),
            min_liq=thresholds.get("min_liquidity", 500),
            min_liq_ratio=thresholds.get("min_liq_mc_ratio", 0.03),
            max_sell_tax=thresholds.get("max_sell_tax", 0.10),
            min_age_min=thresholds.get("min_age_minutes", 10),
        )
        result = self._ai.chat_json(prompt, system_prompt=SYSTEM_PROMPT, temperature=0.2, max_tokens=500)
        if result and isinstance(result, dict):
            self._logger.info(f"FP analysis complete: {result.get('summary', 'no summary')}")
            return result
        return None


class ScoreCalibrator:
    """Auto-calibrates scoring weights based on historical performance."""

    def __init__(self, db: Database, config: dict):
        self._db = db
        self._logger = get_logger()
        self._config = config
        self._last_calibration = 0
        self._calibration_interval = 86400

    def should_calibrate(self) -> bool:
        return time.time() - self._last_calibration >= self._calibration_interval

    def calibrate(self) -> Optional[dict]:
        self._last_calibration = time.time()
        conn = self._db._conn
        c = conn.cursor()
        ranges = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
        range_stats = []
        for low, high in ranges:
            c.execute("""
                SELECT COUNT(*) as total,
                       AVG(pp.pnl_pct) as avg_pnl,
                       SUM(CASE WHEN pp.pnl_pct >= 10 THEN 1 ELSE 0 END) as wins
                FROM push_performance pp
                JOIN push_history ph ON pp.push_id = ph.id
                WHERE ph.score >= ? AND ph.score < ? AND pp.interval_minutes = 60
            """, (low, high))
            row = c.fetchone()
            if row and row[0] > 0:
                range_stats.append({
                    "range": f"{low}-{high}",
                    "total": row[0],
                    "avg_pnl": row[1] or 0,
                    "win_rate": ((row[2] or 0) / row[0]) * 100,
                })
        if not range_stats:
            return None
        suggestions = {}
        current_threshold = self._config.get("push", {}).get("high_score_threshold", 75)
        high_range = [s for s in range_stats if s["range"] in ("80-90", "90-100")]
        low_range = [s for s in range_stats if s["range"] in ("50-60", "60-70")]
        if high_range and low_range:
            high_wr = sum(s["win_rate"] for s in high_range) / len(high_range)
            low_wr = sum(s["win_rate"] for s in low_range) / len(low_range)
            if low_wr > 40:
                suggestions["lower_threshold"] = max(50, current_threshold - 5)
                suggestions["reason"] = f"Low score range win rate {low_wr:.0f}% still good, can lower threshold"
            elif high_wr < 30:
                suggestions["raise_threshold"] = min(90, current_threshold + 5)
                suggestions["reason"] = f"High score range win rate only {high_wr:.0f}%, need higher threshold"
        suggestions["range_stats"] = range_stats
        self._logger.info(f"Score calibration: {suggestions.get('reason', 'no change needed')}")
        return suggestions
