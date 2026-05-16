"""
Token safety checks - GoPlus (EVM) + RugCheck (SOL).
"""

from infra.http_client import HttpClient
from storage.cache import TTLCache
from infra.logger import get_logger

CHAIN_TO_GOPLUS_ID = {"eth": "1", "ethereum": "1", "bsc": "56", "base": "8453"}


class SafetyChecker:
    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()
        self._cache = TTLCache(default_ttl=1800, max_size=2000)
        self._thresholds = config.get("thresholds", {})

    def check(self, chain: str, address: str) -> dict:
        cache_key = f"{chain.lower()}:{address.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if chain.lower() in ("sol", "solana"):
            result = self._check_solana(address)
        else:
            result = self._check_evm(chain, address)
        self._cache.set(cache_key, result)
        return result

    def _check_solana(self, address: str) -> dict:
        result = {"safe": False, "reason": "unable to check", "details": {}}
        resp = self._http.get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report", delay=True, timeout=10)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                mint_auth = data.get("mintAuthority")
                freeze_auth = data.get("freezeAuthority")
                is_safe = not mint_auth and not freeze_auth
                reason = ""
                if mint_auth:
                    reason += "mint authority active; "
                if freeze_auth:
                    reason += "freeze authority active; "
                result = {"safe": is_safe, "reason": reason.strip("; ") if reason else "passed", "details": {"mint_authority": mint_auth is not None, "freeze_authority": freeze_auth is not None, "score": data.get("score", 999)}}
            except Exception:
                pass
        return result

    def _check_evm(self, chain: str, address: str) -> dict:
        result = {"safe": False, "reason": "unable to check", "details": {}}
        chain_id = CHAIN_TO_GOPLUS_ID.get(chain.lower(), "1")
        resp = self._http.get(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}", delay=True, timeout=10)
        if resp and resp.status_code == 200:
            try:
                api_result = resp.json().get("result", {})
                data = api_result.get(address.lower(), {})
                if not data:
                    return result
                honeypot = data.get("is_honeypot", "0") == "1"
                mintable = data.get("is_mintable", "0") == "1"
                sell_tax = float(data.get("sell_tax", "0") or "0")
                buy_tax = float(data.get("buy_tax", "0") or "0")
                owner_change = data.get("can_take_back_ownership", "0") == "1"
                hidden_owner = data.get("hidden_owner", "0") == "1"
                is_proxy = data.get("is_proxy", "0") == "1"
                issues = []
                if honeypot: issues.append("honeypot")
                if mintable: issues.append("mintable")
                if sell_tax > self._thresholds.get("max_sell_tax", 0.10): issues.append(f"sell_tax={sell_tax:.0%}")
                if buy_tax > self._thresholds.get("max_buy_tax", 0.10): issues.append(f"buy_tax={buy_tax:.0%}")
                if owner_change: issues.append("owner_takeback")
                if hidden_owner: issues.append("hidden_owner")
                if is_proxy: issues.append("proxy_contract")
                result = {"safe": len(issues) == 0, "reason": ", ".join(issues) if issues else "passed", "details": {"honeypot": honeypot, "mintable": mintable, "sell_tax": sell_tax, "buy_tax": buy_tax}}
            except Exception:
                pass
        return result

    @property
    def cache_stats(self) -> dict:
        return self._cache.stats
