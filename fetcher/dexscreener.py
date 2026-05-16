"""
DexScreener data fetcher - backup source for token data and price lookups.
"""

import time
from typing import List, Optional

from infra.http_client import HttpClient
from infra.logger import get_logger

CHAIN_MAP = {"eth": "ethereum", "ethereum": "ethereum", "bsc": "bsc", "base": "base", "sol": "solana", "solana": "solana"}


class DexScreenerFetcher:
    """DexScreener API fetcher."""

    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()

    def get_token_info(self, address: str) -> Optional[dict]:
        """Get token info including socials and description."""
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        resp = self._http.get(url, delay=True)
        if resp and resp.status_code == 200:
            try:
                pairs = resp.json().get("pairs", [])
                if not pairs:
                    return None
                pair = pairs[0]
                info = pair.get("info", {})
                twitter, telegram, website = "", "", ""
                for s in info.get("socials", []):
                    if s.get("type") == "twitter":
                        twitter = s.get("url", "")
                    elif s.get("type") == "telegram":
                        telegram = s.get("url", "")
                for w in info.get("websites", []):
                    if w.get("label", "").lower() == "website":
                        website = w.get("url", "")
                    elif not website:
                        website = w.get("url", "")
                return {
                    "description": info.get("description", "") or "",
                    "twitter": twitter, "telegram": telegram, "website": website,
                    "price_usd": pair.get("priceUsd", "0"),
                    "mc": pair.get("marketCap", 0) or pair.get("fdv", 0) or 0,
                    "liquidity": pair.get("liquidity", {}).get("usd", 0) or 0,
                }
            except Exception as e:
                self._logger.debug(f"DexScreener parse error: {e}")
        return None

    def get_token_price(self, address: str) -> dict:
        """Get current price and market cap for a token."""
        info = self.get_token_info(address)
        if info:
            return {"price": float(info.get("price_usd", 0) or 0), "mc": info.get("mc", 0)}
        return {}
