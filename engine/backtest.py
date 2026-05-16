"""
Performance tracking - checks pushed signal results at intervals.
"""

import time
from typing import List

from fetcher.gmgn import GmgnFetcher
from fetcher.dexscreener import DexScreenerFetcher
from storage.database import Database
from infra.logger import get_logger


class PerformanceTracker:
    def __init__(self, gmgn: GmgnFetcher, dexscreener: DexScreenerFetcher, db: Database, config: dict):
        self._gmgn = gmgn
        self._dexscreener = dexscreener
        self._db = db
        self._logger = get_logger()
        self._intervals = config.get("backtest", {}).get("track_intervals", [5, 15, 60, 240, 1440])
        self._last_check = 0
        self._check_interval = 60

    def should_check(self) -> bool:
        return time.time() - self._last_check >= self._check_interval

    def check_performance(self) -> int:
        self._last_check = time.time()
        pending = self._db.get_pending_performance_checks(self._intervals)
        if not pending:
            return 0
        updated = 0
        for record in pending[:10]:
            price_data = self._get_current_price(record["chain"], record["address"])
            if not price_data:
                continue
            current_price = price_data.get("price", 0)
            current_mc = price_data.get("mc", 0)
            push_price = record.get("price_at_push", 0)
            if push_price > 0 and current_price > 0:
                pnl_pct = ((current_price - push_price) / push_price) * 100
            elif record.get("market_cap_at_push", 0) > 0 and current_mc > 0:
                pnl_pct = ((current_mc - record["market_cap_at_push"]) / record["market_cap_at_push"]) * 100
            else:
                continue
            self._db.record_performance(push_id=record["id"], address=record["address"], chain=record["chain"], interval_minutes=record["interval_minutes"], price=current_price, mc=current_mc, pnl_pct=pnl_pct)
            updated += 1
        if updated > 0:
            self._logger.info(f"Performance tracked: {updated} records updated")
        return updated

    def _get_current_price(self, chain: str, address: str) -> dict:
        data = self._gmgn.fetch_token_price(chain, address)
        if data and (data.get("price", 0) > 0 or data.get("mc", 0) > 0):
            return data
        data = self._dexscreener.get_token_price(address)
        if data and (data.get("price", 0) > 0 or data.get("mc", 0) > 0):
            return data
        return {}
