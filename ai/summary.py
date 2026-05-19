"""
AI-powered daily summary and enhanced copywriting.
"""

import time
from typing import Optional

from ai.client import AIClient
from storage.database import Database
from infra.logger import get_logger

DAILY_SUMMARY_SYSTEM = """你是一名链上市场分析师。根据24小时数据生成简洁有洞察力的市场总结。
要求：简洁、有观点、必须使用简体中文回答。仅返回纯文本，不要任何英文标签或前缀。"""

DAILY_SUMMARY_PROMPT = """生成今日链上叙事总结：

24小时数据：
- 扫描代币数：{scanned}
- 推送信号数：{pushed}
- 胜率(1h)：{win_rate:.1f}%
- 平均PnL：{avg_pnl:+.1f}%

热门叙事：
{top_narratives}

最佳信号：
{best_signals}

最差信号：
{worst_signals}

输出格式：
- 主线叙事（1-2句话）
- 市场温度（冷/温/热 + 1句话）
- 明日关注（1-2个方向）
- 参数建议（推送阈值是否需要调整）"""

ENHANCED_MSG_SYSTEM = """你是一名链上信号文案写手。为代币信号撰写简洁的一句话总结。
要求：50字以内，说明为什么值得关注以及最大风险。必须使用简体中文，仅返回纯文本，不要任何英文。"""

ENHANCED_MSG_PROMPT = """代币信号：
名称：{name} ({symbol})
链：{chain}
市值：${mc:,.0f}
流动性：${liq:,.0f}
叙事：{narrative}
描述：{description}
连涨：{rounds}轮 +{pct:.1f}%
评分：{score}/100

用中文写一句投资逻辑 + 一句风险提示："""


class AISummary:
    """AI-powered daily summary generation."""

    def __init__(self, ai_client: AIClient, db: Database, config: dict):
        self._ai = ai_client
        self._db = db
        self._logger = get_logger()
        self._enabled = config.get("ai", {}).get("daily_summary", False)
        self._last_run = 0
        self._run_interval = 86400

    @property
    def enabled(self) -> bool:
        return self._enabled and self._ai.enabled

    def should_run(self) -> bool:
        return self.enabled and (time.time() - self._last_run >= self._run_interval)

    def generate_daily_summary(self) -> Optional[str]:
        if not self.enabled:
            return None
        self._last_run = time.time()
        daily_stats = self._db.get_daily_stats()
        win_rate_data = self._db.get_win_rate(interval_minutes=60, lookback_days=1)
        top_narratives = self._get_top_narratives()
        best_signals = self._get_best_signals()
        worst_signals = self._get_worst_signals()
        prompt = DAILY_SUMMARY_PROMPT.format(
            scanned=daily_stats.get("scanned", 0),
            pushed=daily_stats.get("pushed", 0),
            win_rate=win_rate_data.get("win_rate", 0),
            avg_pnl=win_rate_data.get("avg_pnl", 0),
            top_narratives=top_narratives or "No data",
            best_signals=best_signals or "No data",
            worst_signals=worst_signals or "No data",
        )
        result = self._ai.chat(prompt, system_prompt=DAILY_SUMMARY_SYSTEM, temperature=0.4, max_tokens=500)
        if result:
            self._logger.info("Daily AI summary generated")
        return result

    def _get_top_narratives(self) -> str:
        narratives = self._db.get_recent_narratives(limit=100)
        cutoff = int(time.time()) - 86400
        recent = [n for n in narratives if n.get("last_seen_at", 0) >= cutoff]
        recent.sort(key=lambda x: x.get("token_count", 0), reverse=True)
        lines = []
        for n in recent[:3]:
            lines.append(f"- {n['theme']} (count: {n['token_count']})")
        return "\n".join(lines) if lines else "No clear narratives"

    def _get_best_signals(self) -> str:
        try:
            conn = self._db._conn
            c = conn.cursor()
            cutoff = int(time.time()) - 86400
            c.execute("""
                SELECT ph.name, ph.symbol, ph.chain, pp.pnl_pct
                FROM push_performance pp
                JOIN push_history ph ON pp.push_id = ph.id
                WHERE ph.pushed_at >= ? AND pp.interval_minutes = 60
                ORDER BY pp.pnl_pct DESC LIMIT 3
            """, (cutoff,))
            rows = c.fetchall()
            if rows:
                return "\n".join(f"- {r[0]} ({r[1]}) [{r[2]}] -> {r[3]:+.1f}%" for r in rows)
        except Exception as e:
            self._logger.debug(f"DB query error: {e}")
        return "No data"

    def _get_worst_signals(self) -> str:
        try:
            conn = self._db._conn
            c = conn.cursor()
            cutoff = int(time.time()) - 86400
            c.execute("""
                SELECT ph.name, ph.symbol, ph.chain, pp.pnl_pct
                FROM push_performance pp
                JOIN push_history ph ON pp.push_id = ph.id
                WHERE ph.pushed_at >= ? AND pp.interval_minutes = 60
                ORDER BY pp.pnl_pct ASC LIMIT 3
            """, (cutoff,))
            rows = c.fetchall()
            if rows:
                return "\n".join(f"- {r[0]} ({r[1]}) [{r[2]}] -> {r[3]:+.1f}%" for r in rows)
        except Exception as e:
            self._logger.debug(f"DB query error: {e}")
        return "No data"


class EnhancedCopywriter:
    """AI-enhanced push message copywriting for high-score signals."""

    def __init__(self, ai_client: AIClient, config: dict):
        self._ai = ai_client
        self._logger = get_logger()
        self._enabled = config.get("ai", {}).get("enhanced_copywriting", False)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._ai.enabled

    def enhance(self, name: str, symbol: str, chain: str, mc: float, liq: float,
                narrative: str, description: str, rounds: int, pct: float, score: int) -> Optional[str]:
        if not self.enabled:
            return None
        prompt = ENHANCED_MSG_PROMPT.format(
            name=name, symbol=symbol, chain=chain, mc=mc, liq=liq,
            narrative=narrative,
            description=description[:200] if description else "none",
            rounds=rounds, pct=pct, score=score,
        )
        result = self._ai.chat(prompt, system_prompt=ENHANCED_MSG_SYSTEM, temperature=0.3, max_tokens=100)
        return result
