"""
GMGN data fetcher - primary source for new tokens across ETH/BSC/BASE/SOL.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from infra.http_client import HttpClient
from infra.logger import get_logger

GMGN_HEADERS = {
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}


def build_token(chain: str, t: dict) -> dict:
    """Normalize a GMGN token entry into standard format."""
    addr = t.get("address", "")
    if not addr:
        return None
    mc = t.get("market_cap", 0) or t.get("fdv", 0) or 0
    liq = t.get("liquidity", 0) or 0
    age_ts = t.get("open_timestamp", 0)
    age_h = (time.time() - age_ts) / 3600 if age_ts > 0 else 999

    return {
        "address": addr,
        "chain": chain,
        "name": t.get("name", "?"),
        "symbol": t.get("symbol", "?"),
        "mc": mc,
        "liq": liq,
        "volume": t.get("volume", 0) or 0,
        "holders": t.get("holder_count", 0) or 0,
        "sm": t.get("smart_degen_count", 0) or 0,
        "chg_1h": t.get("price_change_percent1h", 0) or 0,
        "chg_24h": t.get("price_change_percent", 0) or 0,
        "age_h": age_h,
        "price": t.get("price", 0) or 0,
        "buys_1h": t.get("buys", 0) or 0,
        "sells_1h": t.get("sells", 0) or 0,
        "top10_holder_rate": t.get("top_10_holder_rate", 0) or 0,
        "dev_burn": t.get("dev_token_burn_status", None),
        "source": "gmgn",
    }


class GmgnFetcher:
    """Fetches new/trending tokens from GMGN across multiple chains."""

    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()
        self._chains = config.get("scan", {}).get("chains", ["eth", "bsc", "base", "sol"])
        self._max_workers = 6

    def fetch_new_tokens(self) -> List[dict]:
        """Fetch new and trending tokens from all configured chains."""
        all_tokens = []
        seen_addrs = set()
        tasks = []

        for chain in self._chains:
            tasks.append((chain, f"https://gmgn.ai/defi/quotation/v1/rank/{chain}/swaps/1h?orderby=open_timestamp&direction=desc&limit=100"))
            tasks.append((chain, f"https://gmgn.ai/defi/quotation/v1/rank/{chain}/swaps/1h?orderby=swaps&direction=desc&limit=50"))
            tasks.append((chain, f"https://gmgn.ai/defi/quotation/v1/rank/{chain}/swaps/1h?orderby=volume&direction=desc&limit=50"))

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_map = {executor.submit(self._fetch_rank, url): chain for chain, url in tasks}
            for future in as_completed(future_map):
                chain = future_map[future]
                try:
                    data = future.result()
                    for t in data:
                        token = build_token(chain, t)
                        if not token:
                            continue
                        addr = token["address"]
                        if addr in seen_addrs:
                            continue
                        seen_addrs.add(addr)
                        all_tokens.append(token)
                except Exception as e:
                    self._logger.warning(f"GMGN fetch error for {chain}: {e}")

        self._logger.debug(f"GMGN fetched {len(all_tokens)} tokens across {self._chains}")
        return all_tokens

    def fetch_flap_tokens(self) -> List[dict]:
        """Fetch FLAP launchpad tokens from BSC."""
        data = self._fetch_rank("https://gmgn.ai/defi/quotation/v1/rank/bsc/swaps/24h?launchpad=flap&orderby=volume&direction=desc&limit=30")
        tokens = []
        for t in data:
            token = build_token("bsc", t)
            if token:
                token["launchpad"] = "flap"
                tokens.append(token)
        return tokens

    def fetch_token_price(self, chain: str, address: str) -> dict:
        """Fetch current price/mc for a specific token."""
        url = f"https://gmgn.ai/defi/quotation/v1/tokens/top_buyers/{chain}/{address}"
        resp = self._http.get(url, headers=GMGN_HEADERS, delay=True)
        if resp and resp.status_code == 200:
            try:
                data = resp.json().get("data", {}).get("token", {})
                return {"price": data.get("price", 0) or 0, "mc": data.get("market_cap", 0) or 0}
            except Exception as e:
                self._logger.debug(f"GMGN parse error: {e}")
        return {}

    def _fetch_rank(self, url: str) -> List[dict]:
        """Fetch a rank endpoint and return the rank list."""
        resp = self._http.get(url, headers=GMGN_HEADERS, delay=True)
        if resp and resp.status_code == 200:
            try:
                return resp.json().get("data", {}).get("rank", [])
            except Exception as e:
                self._logger.debug(f"GMGN parse error: {e}")
        return []
