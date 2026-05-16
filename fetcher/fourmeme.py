"""
Four.Meme data fetcher - BSC token launch platform.
"""

import time
from typing import List

from infra.http_client import HttpClient
from infra.logger import get_logger


class FourMemeFetcher:
    """Fetches new tokens from Four.Meme (BSC launchpad)."""

    BASE_URL = "https://four.meme/api"

    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()

    def fetch_new_tokens(self, limit: int = 30) -> List[dict]:
        """Fetch recently created tokens from Four.Meme."""
        url = f"{self.BASE_URL}/token/list?page=1&pageSize={limit}&sort=createTime&order=desc"
        resp = self._http.get(url, delay=True)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                tokens_data = data.get("data", {}).get("list", [])
                if isinstance(tokens_data, list):
                    return self._normalize_tokens(tokens_data)
            except Exception as e:
                self._logger.debug(f"Four.Meme parse error: {e}")
        return []

    def _normalize_tokens(self, tokens_data: list) -> List[dict]:
        """Normalize Four.Meme tokens into standard format."""
        tokens = []
        for t in tokens_data:
            addr = t.get("contractAddress", "") or t.get("address", "") or t.get("token", "")
            if not addr:
                continue
            mc = t.get("marketCap", 0) or t.get("market_cap", 0) or 0
            liq = t.get("liquidity", 0) or 0
            created = t.get("createTime", 0) or t.get("created_at", 0)
            if isinstance(created, str):
                try:
                    created = int(created)
                except Exception:
                    created = 0
            if created > 1e12:
                created = created / 1000
            age_h = (time.time() - created) / 3600 if created > 0 else 999
            tokens.append({
                "address": addr, "chain": "bsc",
                "name": t.get("name", "") or t.get("tokenName", "") or "?",
                "symbol": t.get("symbol", "") or t.get("tokenSymbol", "") or "?",
                "mc": mc, "liq": liq,
                "volume": t.get("volume", 0) or 0,
                "holders": t.get("holderCount", 0) or 0, "sm": 0,
                "chg_1h": t.get("priceChange1h", 0) or 0,
                "chg_24h": t.get("priceChange24h", 0) or 0,
                "age_h": age_h, "price": t.get("price", 0) or 0,
                "buys_1h": t.get("buys", 0) or 0, "sells_1h": t.get("sells", 0) or 0,
                "description": t.get("description", "") or "",
                "source": "fourmeme", "launchpad": "fourmeme",
            })
        return tokens
