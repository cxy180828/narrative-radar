"""
Paper trading module - simulates portfolio behavior using live signal pushes.

Hooks into the main scan loop:
  * After a real push lands -> portfolio.try_enter() may open a position.
  * After every momentum.update() -> portfolio.update_prices() drives the
    pyramid exit ladder, the pre-breakeven stop loss and the daily circuit
    breaker.

Everything is *simulated* against the in-memory price the scanner already
fetched, so it adds zero outbound HTTP traffic to GMGN/Pump.fun.
"""

from paper_trading.portfolio import PaperPortfolio  # noqa: F401
from paper_trading.reporter import format_paper_daily_report  # noqa: F401
