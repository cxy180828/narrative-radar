"""
Message formatting for Telegram push notifications.
"""
from typing import Optional

CHAIN_DISPLAY = {"sol": "SOL", "solana": "SOL", "eth": "ETH", "ethereum": "ETH", "bsc": "BSC", "base": "BASE"}
CONFIDENCE_EMOJI = {"high": "\U0001f7e2", "medium": "\U0001f7e1", "low": "\U0001f534"}


def format_momentum_alert(token: dict, pct_gain: float, rounds: int, vol_up: bool, score: int, narrative_tag: str, desc_info: Optional[dict] = None, signal_count: int = 1, ai_insight: Optional[str] = None, push_level: str = "high") -> str:
    chain = CHAIN_DISPLAY.get(token.get("chain", ""), token.get("chain", "").upper())
    confidence = CONFIDENCE_EMOJI.get(push_level, "")
    vol_tag = " (放量)" if vol_up else ""
    msg = f"{confidence} *雷达信号*\n链: {chain}\n\n"
    msg += f"*{token.get('name', '?')}* ({token.get('symbol', '?')})\n`{token.get('address', '')}`\n\n"
    if ai_insight:
        msg += f"_{ai_insight}_\n\n"
    desc = (desc_info or {}).get("description", "")
    if desc:
        msg += f"故事: {desc[:150]}{'...' if len(desc) > 150 else ''}\n\n"
    msg += f"评分: *{score}/100*\n叙事: {narrative_tag}\n连涨: {rounds}轮 +{pct_gain:.1f}%{vol_tag}\n\n"
    msg += "```\n"
    msg += f"市值     ${token.get('mc', 0):>12,.0f}\n"
    msg += f"流动性   ${token.get('liq', 0):>12,.0f}\n"
    msg += f"1h涨幅   {token.get('chg_1h', 0):>+11.1f}%\n"
    if token.get("sm", 0) > 0:
        msg += f"聪明钱   {token['sm']:>12d}\n"
    msg += f"存活     {token.get('age_h', 0):>10.1f}h\n```\n"
    msg += f"信号 #{signal_count}"
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
    msg = "\U0001f4ca *每日报告*\n\n"
    msg += f"扫描: {stats.get('scanned', 0)}\n"
    msg += f"通过过滤: {stats.get('passed', 0)}\n"
    msg += f"动量信号: {stats.get('signals', 0)}\n"
    msg += f"推送: {stats.get('pushed', 0)}\n"
    msg += f"轮次: {stats.get('rounds', 0)}\n\n"
    if win_rate.get("total", 0) > 0:
        msg += f"胜率(1h): {win_rate['win_rate']:.1f}%\n平均PnL: {win_rate['avg_pnl']:+.1f}%\n最佳: {win_rate['max_pnl']:+.1f}%\n最差: {win_rate['min_pnl']:+.1f}%\n"
    if ai_summary:
        msg += f"\n---\n{ai_summary}"
    return msg


def format_startup_message(config: dict) -> str:
    chains = ", ".join(config.get("scan", {}).get("chains", []))
    interval = config.get("scan", {}).get("interval", 30)
    ai_enabled = config.get("ai", {}).get("enabled", False)
    return f"\U0001f680 *Narrative Radar v2 已启动*\n\n链: {chains}\n间隔: {interval}s\nAI: {'已启用' if ai_enabled else '已禁用'}\n逻辑: 动量优先，AI增强\n推送: 评分>=50批量，>=75即时"


def build_alert_buttons(address: str, chain: str) -> list:
    dex_chain = {"sol": "solana", "eth": "ethereum", "bsc": "bsc", "base": "base"}.get(chain.lower(), chain)
    return [
        {"text": "\U0001f4c8 Chart", "url": f"https://dexscreener.com/{dex_chain}/{address}"},
        {"text": "\u274c FP", "callback_data": f"fp:{address}"},
        {"text": "\U0001f6ab Block", "callback_data": f"bl:{address}"},
    ]
