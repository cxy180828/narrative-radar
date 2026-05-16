"""
AI-powered daily summary and enhanced copywriting.
"""

import time
from typing import Optional

from ai.client import AIClient
from storage.database import Database
from infra.logger import get_logger

DAILY_SUMMARY_SYSTEM = """You are an on-chain market analyst. Generate a concise insightful market summary based on 24h data.
Requirements: concise, opinionated. Return plain text only."""

DAILY_SUMMARY_PROMPT = """Generate today's on-chain narrative summary:

24h stats:
- Tokens scanned: {scanned}
- Signals pushed: {pushed}
- Win rate (1h): {win_rate:.1f}%
- Avg PnL: {avg_pnl:+.1f}%

Top narratives:
{top_narratives}

Best signals:
{best_signals}

Worst signals:
{worst_signals}

Output format:
- Main narrative (1-2 sentences)
- Market temperature (cold/warm/hot + 1 sentence)
- Tomorrow focus (1-2 directions)
- Parameter advice (should push threshold be adjusted)"""

ENHANCED_MSG_SYSTEM = """You are an on-chain signal copywriter. Write a concise one-liner for a token signal.
Requirements: under 50 words, explain why it's worth watching and biggest risk. Return plain text only."""

ENHANCED_MSG_PROMPT = """Token signal:
Name: {name} ({symbol})
Chain: {chain}
MC: ${mc:,.0f}
Liquidity: ${liq:,.0f}
Narrative: {narrative}
Description: {description}
Streak: {rounds} rounds +{pct:.1f}%
Score: {score}/100

Write one sentence investment logic + one sentence risk warning:"""


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
        except Exception:
            pass
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
        except Exception:
            pass
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
