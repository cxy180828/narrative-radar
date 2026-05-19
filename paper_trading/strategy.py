"""
Pyramid exit strategy + slippage / fee model for paper trading.

The strategy is deliberately simple and stateless: given a position's current
state (high-water mark, completed exit steps, entry price, current price),
decide what to do next. The portfolio module owns persistence and execution.

Triggers (all evaluated against high-water mark, not current price):
    1. Pre-breakeven stop loss      -> if config.pre_breakeven_stop_loss
       AND no exit steps completed yet
       AND current_pnl_pct < stop_loss_pnl
       => sell 100%

    2. Pyramid exit ladder          -> for each rung not yet executed,
       in order, if HWM_pnl_pct >= rung.trigger_pnl
       => sell rung.sell_pct of remaining

After step 1 (the +100% rung) is hit, the position is "post-breakeven" and
the stop loss is intentionally disabled. The remaining tokens ride free.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ExitAction:
    """A single sell decision the portfolio should execute."""
    sell_pct_of_remaining: float    # 0..100
    trigger: str                    # 'stop_loss' | 'ladder_100' | 'ladder_200' ...
    reason: str                     # human-readable for log/notification


def slippage_pct(position_size_usd: float, liquidity_usd: float, base_pct: float) -> float:
    """Estimate one-way slippage as a percentage.

    Pump.fun-style bonding curves move with sqrt(supply); for the small-cap
    tokens we typically trade, the linear approximation
        position_size / liquidity
    is a reasonable lower bound. We then apply max(base, dynamic). For a token
    with $1,892 liquidity and $50 buy:
        dynamic = 50/1892 = 2.6%
        result  = max(3%, 2.6%) = 3%
    For a token with $200 liquidity:
        dynamic = 50/200 = 25%
        result  = max(3%, 25%) = 25%
    """
    if liquidity_usd <= 0:
        return base_pct + 0.10  # punishment factor for unknown liquidity
    dynamic = position_size_usd / liquidity_usd
    return max(base_pct, dynamic)


def evaluate_exits(
    *,
    entry_price: float,
    current_price: float,
    high_water_mark: float,
    exit_steps_done: int,
    has_been_in_profit: bool,
    exit_ladder: List[dict],
    pre_breakeven_stop_loss: Optional[float],
) -> Optional[ExitAction]:
    """Decide if a position should be (partially) closed this tick.

    Returns at most ONE action per tick. The portfolio re-evaluates on the
    next tick if more sell steps are due.

    Args:
        entry_price: average buy price (after slippage)
        current_price: latest observed price
        high_water_mark: max price seen since entry (for ladder triggers)
        exit_steps_done: how many ladder rungs already executed (0-based)
        has_been_in_profit: True once any ladder rung has executed (post-BE)
        exit_ladder: list of {trigger_pnl, sell_pct} dicts, ascending
        pre_breakeven_stop_loss: e.g. -50.0 means -50%. None disables.
    """
    if entry_price <= 0:
        return None

    current_pnl_pct = (current_price - entry_price) / entry_price * 100.0
    hwm_pnl_pct = (high_water_mark - entry_price) / entry_price * 100.0

    # 1. Pre-breakeven stop loss: only active before first ladder hit
    if pre_breakeven_stop_loss is not None and not has_been_in_profit:
        if current_pnl_pct <= pre_breakeven_stop_loss:
            return ExitAction(
                sell_pct_of_remaining=100.0,
                trigger="stop_loss",
                reason=f"-{abs(current_pnl_pct):.1f}% 触发出本前止损",
            )

    # 2. Ladder: trigger on HWM, execute in order, one per tick
    if exit_steps_done < len(exit_ladder):
        rung = exit_ladder[exit_steps_done]
        if hwm_pnl_pct >= rung["trigger_pnl"]:
            label = _ladder_label(rung["trigger_pnl"], exit_steps_done)
            return ExitAction(
                sell_pct_of_remaining=float(rung["sell_pct"]),
                trigger=f"ladder_{int(rung['trigger_pnl'])}",
                reason=f"+{hwm_pnl_pct:.0f}% 触发{label}，卖出剩余 {rung['sell_pct']}%",
            )

    return None


def _ladder_label(trigger_pnl: float, step_idx: int) -> str:
    """Human-friendly Chinese label for the ladder rung."""
    if step_idx == 0:
        return "翻倍出本"
    if step_idx == 1:
        return "二档减仓"
    if step_idx == 2:
        return "三档减仓"
    return f"第{step_idx + 1}档减仓"
