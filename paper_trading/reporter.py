"""
Paper trading daily report builder.

Aggregates the last 24h of paper_positions / paper_trades activity into a
plain-text Telegram-friendly summary, then appended to the existing daily
report by main.py.
"""

import time
from typing import Optional

from storage.database import Database


def format_paper_daily_report(db: Database, config: dict) -> Optional[str]:
    """Build the paper trading section of the daily report.

    Returns None when paper trading is disabled or there's no activity to
    report (caller should treat as 'omit this section').
    """
    cfg = config.get("paper_trading", {}) or {}
    if not cfg.get("enabled", False):
        return None

    cutoff = int(time.time()) - 86400
    c = db._conn.cursor()

    # --- Activity counters ---
    # Entries today
    c.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE entry_at >= ?",
        (cutoff,),
    )
    entries_today = int(c.fetchone()[0] or 0)

    # Closes today (final PnL classification)
    c.execute(
        """SELECT
              COUNT(*),
              COALESCE(SUM(CASE WHEN final_pnl_usd > 0 THEN 1 ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN final_pnl_usd < 0 THEN 1 ELSE 0 END), 0),
              COALESCE(SUM(final_pnl_usd), 0),
              COALESCE(MAX(final_pnl_usd), 0),
              COALESCE(MIN(final_pnl_usd), 0)
           FROM paper_positions
           WHERE status = 'closed' AND closed_at >= ?""",
        (cutoff,),
    )
    closed, wins, losses, total_pnl_closed, best, worst = c.fetchone()
    closed = int(closed or 0)
    wins = int(wins or 0)
    losses = int(losses or 0)
    total_pnl_closed = float(total_pnl_closed or 0)

    # Realized PnL from all sells today (includes partial sells of still-open positions)
    c.execute(
        "SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_trades "
        "WHERE side = 'sell' AND executed_at >= ?",
        (cutoff,),
    )
    realized_pnl_today = float(c.fetchone()[0] or 0)

    # Stop loss / breakeven counts (within today's closes)
    c.execute(
        """SELECT trigger, COUNT(*) FROM paper_trades pt
           JOIN paper_positions pp ON pp.id = pt.position_id
           WHERE pt.side = 'sell' AND pt.executed_at >= ?
           GROUP BY trigger""",
        (cutoff,),
    )
    by_trigger = {row[0]: int(row[1]) for row in c.fetchall()}
    stops = by_trigger.get("stop_loss", 0)
    breakevens = by_trigger.get("ladder_100", 0)
    pyramid_sells = sum(v for k, v in by_trigger.items() if k.startswith("ladder_") and k != "ladder_100")

    # --- Open positions snapshot ---
    c.execute(
        """SELECT id, symbol, entry_price, initial_amount_usd,
                  initial_token_qty, remaining_qty, realized_pnl_usd,
                  high_water_mark, exit_steps_done
           FROM paper_positions WHERE status = 'open'"""
    )
    open_rows = c.fetchall()
    open_count = len(open_rows)
    capital_in_play = open_count * float(cfg.get("position_size", 50))

    # --- Best / worst closed today ---
    best_line = "-"
    worst_line = "-"
    if closed > 0:
        c.execute(
            """SELECT symbol, final_pnl_usd, final_pnl_pct
               FROM paper_positions
               WHERE status = 'closed' AND closed_at >= ?
               ORDER BY final_pnl_usd DESC LIMIT 1""",
            (cutoff,),
        )
        row = c.fetchone()
        if row:
            best_line = f"{row[0]} {row[1]:+.2f} ({row[2]:+.0f}%)"
        c.execute(
            """SELECT symbol, final_pnl_usd, final_pnl_pct
               FROM paper_positions
               WHERE status = 'closed' AND closed_at >= ?
               ORDER BY final_pnl_usd ASC LIMIT 1""",
            (cutoff,),
        )
        row = c.fetchone()
        if row:
            worst_line = f"{row[0]} {row[1]:+.2f} ({row[2]:+.0f}%)"

    # --- Lifetime equity ---
    c.execute("SELECT COALESCE(SUM(final_pnl_usd), 0) FROM paper_positions WHERE status = 'closed'")
    lifetime_realized = float(c.fetchone()[0] or 0)
    initial_capital = float(cfg.get("initial_capital", 10000))
    equity = initial_capital + lifetime_realized

    win_rate = (wins / closed * 100.0) if closed > 0 else 0.0

    lines = []
    lines.append("\n\U0001f4bc *虚拟盘日报*")
    lines.append(f"账户净值: ${equity:,.2f} (初始 ${initial_capital:,.0f}, 累计 {lifetime_realized:+,.2f})")
    lines.append(f"24h 实现盈亏: {realized_pnl_today:+.2f}")
    lines.append("")
    lines.append(f"开仓: {entries_today} 笔  |  平仓: {closed} 笔")
    if closed > 0:
        lines.append(f"  胜: {wins}  负: {losses}  胜率 {win_rate:.0f}%")
        lines.append(f"  最佳: {best_line}")
        lines.append(f"  最差: {worst_line}")
    lines.append(f"出本: {breakevens}  减仓: {pyramid_sells}  止损: {stops}")
    lines.append("")
    lines.append(f"未平仓: {open_count} 笔  |  占用资金: ${capital_in_play:,.0f}")
    if open_rows:
        # Show top-3 floating positions by ladder progress
        rows_sorted = sorted(open_rows, key=lambda r: r[8], reverse=True)[:3]
        for r in rows_sorted:
            pos_id, sym, entry_p, init_usd, init_qty, rem_qty, realized, hwm, steps = r
            hwm_pct = ((hwm - entry_p) / entry_p * 100.0) if entry_p > 0 else 0
            remaining_pct = (rem_qty / init_qty * 100.0) if init_qty > 0 else 0
            lines.append(
                f"  - #{pos_id} {sym}: 高点 +{hwm_pct:.0f}% | 剩 {remaining_pct:.0f}% | 已落袋 ${realized:.2f} | 档位 {steps}/4"
            )

    return "\n".join(lines)
