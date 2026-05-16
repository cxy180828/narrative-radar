"""
HTTP client with session reuse, retry, UA rotation, and rate limiting.
"""

import random
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from infra.logger import get_logger

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0",
]


class HttpClient:
    """Shared HTTP client with retry, UA rotation, and random delays."""

    def __init__(
        self,
        timeout: int = 15,
        retries: int = 3,
        backoff_factor: float = 1.0,
        status_forcelist: list = None,
        ua_rotation: bool = True,
        random_delay_min: float = 0.3,
        random_delay_max: float = 1.5,
    ):
        self._logger = get_logger()
        self._timeout = timeout
        self._ua_rotation = ua_rotation
        self._delay_min = random_delay_min
        self._delay_max = random_delay_max

        # Request counters for observability
        self._request_count = 0
        self._error_count = 0
        self._last_request_time = 0

        # Build session with retry
        self._session = requests.Session()
        retry_strategy = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist or [429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _get_headers(self, extra_headers: dict = None) -> dict:
        headers = {
            "Accept": "application/json",
            "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8", "zh-CN,zh;q=0.9,en;q=0.8"]),
            "Accept-Encoding": "gzip, deflate, br",
        }
        if self._ua_rotation:
            headers["User-Agent"] = random.choice(USER_AGENTS)
        else:
            headers["User-Agent"] = USER_AGENTS[0]
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _random_delay(self):
        """Add a small random delay between requests to avoid detection."""
        delay = random.uniform(self._delay_min, self._delay_max)
        time.sleep(delay)

    def get(self, url: str, headers: dict = None, timeout: int = None, delay: bool = True, **kwargs) -> Optional[requests.Response]:
        """GET request with retry, rotation and delay."""
        if delay:
            self._random_delay()
        self._request_count += 1
        try:
            resp = self._session.get(
                url,
                headers=self._get_headers(headers),
                timeout=timeout or self._timeout,
                **kwargs,
            )
            if resp.status_code == 429:
                self._logger.warning(f"Rate limited (429) on {url}")
                self._error_count += 1
            return resp
        except requests.exceptions.RequestException as e:
            self._error_count += 1
            self._logger.warning(f"HTTP GET error: {url} - {e}")
            return None

    def post(self, url: str, headers: dict = None, timeout: int = None, delay: bool = True, **kwargs) -> Optional[requests.Response]:
        """POST request with retry, rotation and delay."""
        if delay:
            self._random_delay()
        self._request_count += 1
        try:
            resp = self._session.post(
                url,
                headers=self._get_headers(headers),
                timeout=timeout or self._timeout,
                **kwargs,
            )
            if resp.status_code == 429:
                self._logger.warning(f"Rate limited (429) on POST {url}")
                self._error_count += 1
            return resp
        except requests.exceptions.RequestException as e:
            self._error_count += 1
            self._logger.warning(f"HTTP POST error: {url} - {e}")
            return None

    @property
    def stats(self) -> dict:
        return {
            "requests": self._request_count,
            "errors": self._error_count,
            "error_rate": (self._error_count / max(self._request_count, 1)) * 100,
        }
