"""
PaperPortfolio - in-process simulation of a small portfolio reacting to
real-time signal pushes.

Two entry points called from main.py:
    * try_enter(token, score, push_id) -> bool
        Called immediately after a signal is pushed. Opens at most one
        position per token (no averaging up on follow-up signals).
    * update_prices(tokens) -> None
        Called every scan round with the freshest snapshot. Drives the
        exit ladder, the pre-breakeven stop loss and the daily circuit
        breaker.

State lives in two SQLite tables (paper_positions, paper_trades) so that a
process restart does not lose open positions.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from infra.logger import get_logger
from notify.telegram import TelegramNotifier
from notify.feishu import FeishuNotifier
from paper_trading.strategy import ExitAction, evaluate_exits, slippage_pct
from storage.database import Database


CHAIN_DISPLAY = {"sol": "SOL", "solana": "SOL", "eth": "ETH", "ethereum": "ETH", "bsc": "BSC", "base": "BASE"}


@dataclass
class _Position:
    """Lightweight in-memory mirror of a paper_positions row."""
    id: int
    address: str
    chain: str
    name: str
    symbol: str
    entry_price: float
    initial_amount_usd: float
    initial_token_qty: float
    high_water_mark: float
    exit_steps_done: int
    realized_pnl_usd: float
    remaining_qty: float
    entry_at: int
    push_id: Optional[int]


class PaperPortfolio:
    """Simulated portfolio glued to live scanner signals."""

    def __init__(
        self,
        db: Database,
        config: dict,
        telegram: TelegramNotifier,
        feishu: FeishuNotifier,
    ):
        self._db = db
        self._tg = telegram
        self._feishu = feishu
        self._logger = get_logger()

        cfg = config.get("paper_trading", {}) or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.position_size: float = float(cfg.get("position_size", 50))
        self.max_concurrent: int = int(cfg.get("max_concurrent_positions", 10))
        self.min_score_to_buy: int = int(cfg.get("min_score_to_buy", 50))
        self.base_slippage: float = float(cfg.get("base_slippage", 0.03))
        self.fee: float = float(cfg.get("fee", 0.005))
        self.pre_be_sl: Optional[float] = cfg.get("pre_breakeven_stop_loss", -50)
        self.exit_ladder: List[dict] = list(cfg.get("exit_ladder", [
            {"trigger_pnl": 100, "sell_pct": 50},
            {"trigger_pnl": 200, "sell_pct": 30},
            {"trigger_pnl": 400, "sell_pct": 30},
            {"trigger_pnl": 800, "sell_pct": 50},
        ]))
        # Sort to be defensive
        self.exit_ladder.sort(key=lambda r: r["trigger_pnl"])
        self.daily_circuit: float = float(cfg.get("daily_loss_circuit_breaker", 300))

        # Circuit breaker state (in-memory; resets on restart - acceptable)
        self._breaker_until_ts: float = 0.0

        if self.enabled:
            self._ensure_schema()
            self._logger.info(
                f"PaperPortfolio enabled: size=${self.position_size} "
                f"max={self.max_concurrent} min_score={self.min_score_to_buy} "
                f"ladder={[r['trigger_pnl'] for r in self.exit_ladder]}"
            )

    # ----------------------- schema ----------------------- #

    def _ensure_schema(self):
        c = self._db._conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            chain TEXT,
            name TEXT,
            symbol TEXT,
            entry_price REAL,
            entry_mc REAL,
            entry_at INTEGER,
            push_id INTEGER,
            initial_amount_usd REAL DEFAULT 50,
            initial_token_qty REAL,
            high_water_mark REAL,
            exit_steps_done INTEGER DEFAULT 0,
            realized_pnl_usd REAL DEFAULT 0,
            remaining_qty REAL,
            status TEXT DEFAULT 'open',
            closed_at INTEGER,
            final_pnl_usd REAL,
            final_pnl_pct REAL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            side TEXT NOT NULL,
            trigger TEXT,
            price REAL,
            qty REAL,
            amount_usd REAL,
            slippage_pct REAL,
            fee_usd REAL,
            pnl_usd REAL,
            executed_at INTEGER,
            FOREIGN KEY(position_id) REFERENCES paper_positions(id)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pp_status ON paper_positions(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pp_addr ON paper_positions(address)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pt_pos ON paper_trades(position_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pt_time ON paper_trades(executed_at)")
        self._db._conn.commit()

    # ----------------------- entry ----------------------- #

    def try_enter(self, token: dict, score: int, push_id: Optional[int] = None) -> bool:
        """Maybe open a new position for `token`. Returns True if entered."""
        if not self.enabled:
            return False
        if score < self.min_score_to_buy:
            self._logger.debug(
                f"[paper] skip entry: {token.get('symbol')} score={score} "
                f"< min={self.min_score_to_buy}"
            )
            return False
        if self._is_circuit_breaker_active():
            self._logger.info(
                f"[paper] skip entry {token.get('symbol')}: circuit breaker active"
            )
            return False
        if self._already_holding(token["address"]):
            self._logger.debug(f"[paper] skip entry {token.get('symbol')}: already holding")
            return False
        open_count = self._open_position_count()
        if open_count >= self.max_concurrent:
            self._logger.info(
                f"[paper] skip entry {token.get('symbol')}: at capacity {open_count}/{self.max_concurrent}"
            )
            return False

        entry_price_raw = float(token.get("price") or 0)
        liq = float(token.get("liq") or 0)
        if entry_price_raw <= 0:
            self._logger.warning(
                f"[paper] skip entry {token.get('symbol')}: no price quote"
            )
            return False

        slip = slippage_pct(self.position_size, liq, self.base_slippage)
        # Buy side: pay more, get less. Effective entry = price * (1 + slip).
        effective_price = entry_price_raw * (1.0 + slip)
        fee_usd = self.position_size * self.fee
        net_after_fee = self.position_size - fee_usd
        qty = net_after_fee / effective_price if effective_price > 0 else 0.0
        if qty <= 0:
            return False

        now = int(time.time())
        c = self._db._conn.cursor()
        c.execute(
            """INSERT INTO paper_positions
               (address, chain, name, symbol, entry_price, entry_mc, entry_at,
                push_id, initial_amount_usd, initial_token_qty,
                high_water_mark, exit_steps_done, realized_pnl_usd,
                remaining_qty, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open')""",
            (
                token["address"], token.get("chain"), token.get("name"),
                token.get("symbol"), effective_price, token.get("mc", 0),
                now, push_id, self.position_size, qty,
                effective_price, 0, 0.0, qty,
            ),
        )
        position_id = c.lastrowid
        c.execute(
            """INSERT INTO paper_trades
               (position_id, side, trigger, price, qty, amount_usd,
                slippage_pct, fee_usd, executed_at)
               VALUES (?, 'buy', 'signal', ?, ?, ?, ?, ?, ?)""",
            (position_id, effective_price, qty, self.position_size,
             slip * 100, fee_usd, now),
        )
        self._db._conn.commit()

        self._logger.info(
            f"[paper] BUY {token.get('symbol')} qty={qty:.4f} @ "
            f"${effective_price:.8f} (slip={slip*100:.1f}%) pos_id={position_id}"
        )
        self._notify_entry(token, position_id, effective_price, slip, fee_usd, score, open_count + 1)
        return True

    # ----------------------- price tick ----------------------- #

    def update_prices(self, tokens: List[dict]) -> None:
        """Process exit ladder for all open positions using the latest tokens.

        We deliberately DO NOT call out to GMGN to fetch missing prices: we
        only act on tokens that the main scan loop already returned. Prices
        for inactive tokens just go stale. Worst case the position triggers
        on a slightly older price - acceptable for paper trading.
        """
        if not self.enabled:
            return
        positions = self._load_open_positions()
        if not positions:
            return

        # Index latest price snapshots by address
        latest_by_addr: Dict[str, dict] = {t["address"]: t for t in tokens if t.get("address")}

        for pos in positions:
            t = latest_by_addr.get(pos.address)
            if not t:
                continue
            price = float(t.get("price") or 0)
            if price <= 0:
                continue
            # Update HWM (and persist if it grew)
            if price > pos.high_water_mark:
                pos.high_water_mark = price
                self._db._conn.execute(
                    "UPDATE paper_positions SET high_water_mark = ? WHERE id = ?",
                    (price, pos.id),
                )
                self._db._conn.commit()

            action = evaluate_exits(
                entry_price=pos.entry_price,
                current_price=price,
                high_water_mark=pos.high_water_mark,
                exit_steps_done=pos.exit_steps_done,
                has_been_in_profit=(pos.exit_steps_done > 0),
                exit_ladder=self.exit_ladder,
                pre_breakeven_stop_loss=self.pre_be_sl,
            )
            if action:
                self._execute_sell(pos, t, price, action)

    # ----------------------- sell execution ----------------------- #

    def _execute_sell(self, pos: _Position, token: dict, raw_price: float, action: ExitAction) -> None:
        """Realize part (or all) of a position."""
        sell_qty = pos.remaining_qty * (action.sell_pct_of_remaining / 100.0)
        if sell_qty <= 0:
            return
        liq = float(token.get("liq") or 0)
        # Sell side: receive less. Use the same slippage model as buy.
        slip = slippage_pct(sell_qty * raw_price, liq, self.base_slippage)
        effective_price = raw_price * (1.0 - slip)
        gross_usd = sell_qty * effective_price
        fee_usd = gross_usd * self.fee
        net_usd = gross_usd - fee_usd

        # PnL of THIS trade vs entry cost basis of THIS slice
        cost_basis_usd = sell_qty * pos.entry_price
        trade_pnl = net_usd - cost_basis_usd

        new_remaining = pos.remaining_qty - sell_qty
        new_realized = pos.realized_pnl_usd + net_usd
        new_steps = pos.exit_steps_done if action.trigger == "stop_loss" else pos.exit_steps_done + 1
        is_fully_closed = new_remaining <= max(pos.initial_token_qty * 1e-6, 1e-12) or action.trigger == "stop_loss"

        now = int(time.time())
        c = self._db._conn.cursor()
        c.execute(
            """INSERT INTO paper_trades
               (position_id, side, trigger, price, qty, amount_usd,
                slippage_pct, fee_usd, pnl_usd, executed_at)
               VALUES (?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pos.id, action.trigger, effective_price, sell_qty, net_usd,
             slip * 100, fee_usd, trade_pnl, now),
        )

        if is_fully_closed:
            # Final PnL = realized cash out - initial buy amount
            final_pnl = new_realized - pos.initial_amount_usd
            final_pct = (final_pnl / pos.initial_amount_usd * 100.0) if pos.initial_amount_usd > 0 else 0
            c.execute(
                """UPDATE paper_positions
                   SET realized_pnl_usd = ?, remaining_qty = 0, exit_steps_done = ?,
                       status = 'closed', closed_at = ?, final_pnl_usd = ?, final_pnl_pct = ?
                   WHERE id = ?""",
                (new_realized, new_steps, now, final_pnl, final_pct, pos.id),
            )
        else:
            c.execute(
                """UPDATE paper_positions
                   SET realized_pnl_usd = ?, remaining_qty = ?, exit_steps_done = ?
                   WHERE id = ?""",
                (new_realized, new_remaining, new_steps, pos.id),
            )
        self._db._conn.commit()

        self._logger.info(
            f"[paper] SELL pos_id={pos.id} sym={pos.symbol} "
            f"trigger={action.trigger} qty={sell_qty:.4f} net=${net_usd:.2f} "
            f"trade_pnl=${trade_pnl:.2f} closed={is_fully_closed}"
        )
        self._notify_exit(pos, raw_price, action, sell_qty, net_usd, trade_pnl,
                          is_fully_closed, new_realized)

    # ----------------------- helpers ----------------------- #

    def _load_open_positions(self) -> List[_Position]:
        c = self._db._conn.cursor()
        c.execute(
            """SELECT id, address, chain, name, symbol, entry_price,
                      initial_amount_usd, initial_token_qty,
                      high_water_mark, exit_steps_done, realized_pnl_usd,
                      remaining_qty, entry_at, push_id
               FROM paper_positions WHERE status = 'open'"""
        )
        rows = c.fetchall()
        return [_Position(*r) for r in rows]

    def _already_holding(self, address: str) -> bool:
        c = self._db._conn.cursor()
        c.execute(
            "SELECT 1 FROM paper_positions WHERE address = ? AND status = 'open' LIMIT 1",
            (address,),
        )
        return c.fetchone() is not None

    def _open_position_count(self) -> int:
        c = self._db._conn.cursor()
        c.execute("SELECT COUNT(*) FROM paper_positions WHERE status = 'open'")
        return int(c.fetchone()[0] or 0)

    def _is_circuit_breaker_active(self) -> bool:
        if self._breaker_until_ts and time.time() < self._breaker_until_ts:
            return True
        # Refresh: compute today's net PnL (realized + unrealized snapshot)
        cutoff = int(time.time()) - 86400
        c = self._db._conn.cursor()
        # Sum of trade PnL today (sells only have non-null pnl_usd)
        c.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_trades "
            "WHERE side = 'sell' AND executed_at >= ?",
            (cutoff,),
        )
        realized_today = float(c.fetchone()[0] or 0)
        if realized_today <= -abs(self.daily_circuit):
            self._breaker_until_ts = time.time() + 86400
            self._logger.warning(
                f"[paper] daily loss circuit breaker tripped: realized={realized_today:.2f}"
            )
            try:
                self._tg.send(
                    f"\u26a0\ufe0f *虚拟盘风控触发*\n\n"
                    f"24h 实现亏损 ${abs(realized_today):.0f} > ${self.daily_circuit:.0f}\n"
                    f"自动停手 24 小时，期间不再开新仓。\n"
                    f"已开仓位继续按策略管理。"
                )
            except Exception:
                pass
            return True
        return False

    # ----------------------- notifications ----------------------- #

    def _notify_entry(self, token: dict, position_id: int, price: float,
                       slip: float, fee_usd: float, score: int, open_after: int) -> None:
        chain = CHAIN_DISPLAY.get(token.get("chain", ""), token.get("chain", "").upper())
        msg = (
            f"\U0001f4c8 *虚拟买入* ${self.position_size:.0f}\n"
            f"*{token.get('name','?')}* ({token.get('symbol','?')}) [{chain}]\n\n"
            f"入场价: `${price:.8f}`\n"
            f"滑点: -{slip*100:.1f}% | 手续费: ${fee_usd:.2f}\n"
            f"评分: {score}/100\n\n"
            f"持仓: {open_after}/{self.max_concurrent}  仓位号 #{position_id}\n"
            f"`{token.get('address','')}`"
        )
        try:
            self._tg.send(msg)
        except Exception as e:
            self._logger.debug(f"[paper] tg entry notify failed: {e}")
        if self._feishu.enabled:
            try:
                self._feishu.send(msg)
            except Exception as e:
                self._logger.debug(f"[paper] feishu entry notify failed: {e}")

    def _notify_exit(self, pos: _Position, raw_price: float, action: ExitAction,
                      sell_qty: float, net_usd: float, trade_pnl: float,
                      is_closed: bool, total_realized: float) -> None:
        held_seconds = int(time.time()) - pos.entry_at
        hours = held_seconds // 3600
        minutes = (held_seconds % 3600) // 60
        held_str = f"{hours}h{minutes}m" if hours else f"{minutes}m"

        if action.trigger == "stop_loss":
            icon = "\U0001f6d1"  # 🛑
            title = "止损"
        elif action.trigger == "ladder_100":
            icon = "\U0001f4b0"  # 💰
            title = "翻倍出本"
        else:
            icon = "\U0001f680"  # 🚀
            title = "金字塔减仓"

        # Final PnL summary if fully closed
        if is_closed:
            final_pnl = total_realized - pos.initial_amount_usd
            final_pct = (final_pnl / pos.initial_amount_usd * 100.0) if pos.initial_amount_usd > 0 else 0
            tail = (
                f"\n\u2500\u2500 已平仓 \u2500\u2500\n"
                f"累计落袋: ${total_realized:.2f}\n"
                f"最终盈亏: {'+' if final_pnl >= 0 else ''}${final_pnl:.2f} ({final_pct:+.1f}%)"
            )
        else:
            current_pct = (raw_price - pos.entry_price) / pos.entry_price * 100.0 if pos.entry_price > 0 else 0
            remaining_pct = (pos.remaining_qty - sell_qty) / pos.initial_token_qty * 100.0 if pos.initial_token_qty > 0 else 0
            tail = (
                f"\n剩余仓位: {remaining_pct:.0f}% | 当前盈亏: {current_pct:+.1f}%\n"
                f"累计落袋: ${total_realized:.2f}"
            )

        msg = (
            f"{icon} *{title}* {pos.symbol}\n\n"
            f"{action.reason}\n"
            f"卖出价: `${raw_price:.8f}`\n"
            f"本笔到账: ${net_usd:.2f} (盈亏 {'+' if trade_pnl >= 0 else ''}${trade_pnl:.2f})\n"
            f"持仓时长: {held_str}"
            f"{tail}"
        )
        try:
            self._tg.send(msg)
        except Exception as e:
            self._logger.debug(f"[paper] tg exit notify failed: {e}")
        if self._feishu.enabled:
            try:
                self._feishu.send(msg)
            except Exception as e:
                self._logger.debug(f"[paper] feishu exit notify failed: {e}")
