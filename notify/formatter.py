"""
Message formatting for Telegram push notifications.
"""
from typing import Optional

CHAIN_DISPLAY = {"sol": "SOL", "solana": "SOL", "eth": "ETH", "ethereum": "ETH", "bsc": "BSC", "base": "BASE"}
CONFIDENCE_EMOJI = {"high": "\U0001f7e2", "medium": "\U0001f7e1", "low": "\U0001f534"}


def format_momentum_alert(token: dict, pct_gain: float, rounds: int, vol_up: bool, score: int, narrative_tag: str, desc_info: Optional[dict] = None, signal_count: int = 1, ai_insight: Optional[str] = None, push_level: str = "high") -> str:
    chain = CHAIN_DISPLAY.get(token.get("chain", ""), token.get("chain", "").upper())
    confidence = CONFIDENCE_EMOJI.get(push_level, "")
    vol_tag = " (vol up)" if vol_up else ""
    msg = f"{confidence} *Radar Signal*\nChain: {chain}\n\n"
    msg += f"*{token.get('name', '?')}* ({token.get('symbol', '?')})\n`{token.get('address', '')}`\n\n"
    if ai_insight:
        msg += f"_{ai_insight}_\n\n"
    desc = (desc_info or {}).get("description", "")
    if desc:
        msg += f"Story: {desc[:150]}{'...' if len(desc) > 150 else ''}\n\n"
    msg += f"Score: *{score}/100*\nNarrative: {narrative_tag}\nStreak: {rounds} rounds +{pct_gain:.1f}%{vol_tag}\n\n"
    msg += "```\n"
    msg += f"MC       ${token.get('mc', 0):>12,.0f}\n"
    msg += f"Liq      ${token.get('liq', 0):>12,.0f}\n"
    msg += f"1h Chg   {token.get('chg_1h', 0):>+11.1f}%\n"
    if token.get("sm", 0) > 0:
        msg += f"SmartMon {token['sm']:>12d}\n"
    msg += f"Age      {token.get('age_h', 0):>10.1f}h\n```\n"
    msg += f"Signal #{signal_count}"
    links = []
    addr = token.get("address", "")
    chain_raw = token.get("chain", "")
    dex_chain = {"sol": "solana", "eth": "ethereum", "bsc": "bsc", "base": "base"}.get(chain_raw, chain_raw)
    if addr:
        links.append(f"[GMGN](https://gmgn.ai/{chain_raw}/token/{addr})")
        links.append(f"[DEX](https://dexscreener.com/{dex_chain}/{addr})")
    if (desc_info or {}).get("twitter"):
        links.append(f"[Twitter]({desc_info['twitter']})")
    if links:
        msg += "\n" + " | ".join(links)
    return msg


def format_daily_report(stats: dict, win_rate: dict, ai_summary: Optional[str] = None) -> str:
    msg = "\U0001f4ca *Daily Report*\n\n"
    msg += f"Scanned: {stats.get('scanned', 0)}\nPushed: {stats.get('pushed', 0)}\nRounds: {stats.get('rounds', 0)}\n\n"
    if win_rate.get("total", 0) > 0:
        msg += f"Win rate (1h): {win_rate['win_rate']:.1f}%\nAvg PnL: {win_rate['avg_pnl']:+.1f}%\nBest: {win_rate['max_pnl']:+.1f}%\nWorst: {win_rate['min_pnl']:+.1f}%\n"
    if ai_summary:
        msg += f"\n---\n{ai_summary}"
    return msg


def format_startup_message(config: dict) -> str:
    chains = ", ".join(config.get("scan", {}).get("chains", []))
    interval = config.get("scan", {}).get("interval", 30)
    ai_enabled = config.get("ai", {}).get("enabled", False)
    return f"\U0001f680 *Narrative Radar v2 Started*\n\nChains: {chains}\nInterval: {interval}s\nAI: {'enabled' if ai_enabled else 'disabled'}\nLogic: Momentum-first, AI-enhanced\nPush: Score >= 50 batch, >= 75 immediate"


def build_alert_buttons(address: str, chain: str) -> list:
    dex_chain = {"sol": "solana", "eth": "ethereum", "bsc": "bsc", "base": "base"}.get(chain.lower(), chain)
    return [
        {"text": "\U0001f4c8 Chart", "url": f"https://dexscreener.com/{dex_chain}/{address}"},
        {"text": "\u274c FP", "callback_data": f"fp:{address}"},
        {"text": "\U0001f6ab Block", "callback_data": f"bl:{address}"},
    ]
