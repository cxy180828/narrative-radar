"""
Startup self-check and runtime health monitoring.
"""

import os
import shutil
import time

from infra.logger import get_logger
from infra.http_client import HttpClient


class HealthChecker:
    """Performs startup checks and runtime health monitoring."""

    def __init__(self, http_client: HttpClient, config: dict):
        self._logger = get_logger()
        self._http = http_client
        self._config = config
        self._checks_passed = {}
        self._last_check_time = 0

    def startup_check(self) -> bool:
        """Run all startup checks. Returns True if all critical checks pass."""
        self._logger.info("Running startup health checks...")
        all_ok = True

        # 1. Disk space check
        disk = shutil.disk_usage("/")
        free_gb = disk.free / (1024 ** 3)
        if free_gb < 0.5:
            self._logger.error(f"Low disk space: {free_gb:.2f} GB free")
            all_ok = False
        else:
            self._logger.info(f"Disk space OK: {free_gb:.2f} GB free")
        self._checks_passed["disk"] = free_gb >= 0.5

        # 2. GMGN reachability
        gmgn_base = os.environ.get("GMGN_BASE_URL", "https://gmgn.ai").rstrip("/")
        gmgn_headers = {"Referer": "https://gmgn.ai/", "Origin": "https://gmgn.ai"}
        gmgn_ok = self._check_url(
            f"{gmgn_base}/defi/quotation/v1/rank/eth/swaps/1h?limit=1",
            "GMGN",
            headers=gmgn_headers,
        )
        self._checks_passed["gmgn"] = gmgn_ok
        if not gmgn_ok:
            self._logger.warning("GMGN API unreachable - will retry during operation")

        # 3. Telegram connectivity
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        tg_ok = False
        if tg_token:
            resp = self._http.get(
                f"https://api.telegram.org/bot{tg_token}/getMe",
                delay=False,
            )
            tg_ok = resp is not None and resp.status_code == 200
        if not tg_ok:
            self._logger.warning("Telegram Bot not configured or unreachable")
        else:
            self._logger.info("Telegram Bot connection OK")
        self._checks_passed["telegram"] = tg_ok

        # 4. AI provider check (optional)
        ai_cfg = self._config.get("ai", {})
        if ai_cfg.get("enabled"):
            ai_ok = False
            for provider in ai_cfg.get("providers", []):
                key_env = provider.get("api_key_env", "")
                if os.environ.get(key_env):
                    ai_ok = True
                    self._logger.info(f"AI provider '{provider['name']}' key found")
                    break
            if not ai_ok:
                self._logger.warning("No AI provider API key configured - AI features disabled")
            self._checks_passed["ai"] = ai_ok

        self._last_check_time = time.time()
        status = "ALL PASSED" if all_ok else "SOME WARNINGS"
        self._logger.info(f"Startup checks: {status} - {self._checks_passed}")
        return all_ok

    def _check_url(self, url: str, name: str, headers: dict = None) -> bool:
        """Check if a URL is reachable."""
        kwargs = {"delay": False, "timeout": 10}
        if headers:
            kwargs["headers"] = headers
        resp = self._http.get(url, **kwargs)
        if resp is not None and resp.status_code == 200:
            self._logger.info(f"{name} API reachable")
            return True
        self._logger.warning(f"{name} API unreachable")
        return False

    def check_disk_space(self) -> bool:
        """Runtime disk space check."""
        disk = shutil.disk_usage("/")
        free_gb = disk.free / (1024 ** 3)
        return free_gb >= 0.2

    @property
    def status(self) -> dict:
        return {
            "checks": self._checks_passed,
            "last_check": self._last_check_time,
        }
