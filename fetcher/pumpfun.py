"""
pump.fun data fetcher - Solana token launch platform.
"""

import time
from typing import List, Optional

from infra.http_client import HttpClient
from infra.logger import get_logger


class PumpFunFetcher:
    """Fetches new tokens and descriptions from pump.fun (Solana)."""

    BASE_URL = "https://frontend-api-v3.pump.fun"

    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()

    def fetch_new_tokens(self, limit: int = 50) -> List[dict]:
        """Fetch recently created tokens from pump.fun."""
        url = f"{self.BASE_URL}/coins?offset=0&limit={limit}&sort=created_timestamp&order=DESC&includeNsfw=false"
        resp = self._http.get(url, delay=True)
        if resp and resp.status_code == 200:
            try:
                coins = resp.json()
                if isinstance(coins, list):
                    return self._normalize_coins(coins)
            except Exception as e:
                self._logger.debug(f"pump.fun parse error: {e}")
        return []

    def get_token_description(self, address: str) -> Optional[dict]:
        """Get detailed token info including description and socials."""
        url = f"{self.BASE_URL}/coins/{address}"
        resp = self._http.get(url, delay=True)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                return {
                    "description": (data.get("description", "") or "").strip(),
                    "twitter": data.get("twitter", "") or "",
                    "telegram": data.get("telegram", "") or "",
                    "website": data.get("website", "") or "",
                }
            except Exception as e:
                self._logger.debug(f"pump.fun description fetch error: {e}")
        return None

    def _normalize_coins(self, coins: list) -> List[dict]:
        """Normalize pump.fun coins into standard token format."""
        tokens = []
        for c in coins:
            addr = c.get("mint", "") or c.get("address", "")
            if not addr:
                continue
            mc = c.get("usd_market_cap", 0) or 0
            created = c.get("created_timestamp", 0)
            if isinstance(created, str):
                try:
                    created = int(created) / 1000
                except Exception:
                    created = 0
            elif created > 1e12:
                created = created / 1000
            age_h = (time.time() - created) / 3600 if created > 0 else 999
            tokens.append({
                "address": addr, "chain": "sol",
                "name": c.get("name", "?"), "symbol": c.get("symbol", "?"),
                "mc": mc, "liq": mc * 0.1, "volume": 0, "holders": 0, "sm": 0,
                "chg_1h": 0, "chg_24h": 0, "age_h": age_h, "price": 0,
                "buys_1h": 0, "sells_1h": 0,
                "description": (c.get("description", "") or "").strip(),
                "source": "pumpfun", "launchpad": "pumpfun",
            })
        return tokens
