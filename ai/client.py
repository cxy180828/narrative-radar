"""
Unified LLM client with multi-provider fallback and rate limiting.
Supports: Groq (free tier), DeepSeek (cheap), Gemini (free tier).
All providers use OpenAI-compatible API format.
"""

import json
import os
import time
import threading
from typing import Optional, List

from infra.logger import get_logger
from infra.http_client import HttpClient


class RateLimiter:
    """Simple token-bucket rate limiter per provider."""

    def __init__(self, max_rpm: int = 30, max_rpd: int = 14000):
        self._max_rpm = max_rpm
        self._max_rpd = max_rpd
        self._minute_requests = []
        self._day_requests = []
        self._lock = threading.Lock()

    def can_request(self) -> bool:
        now = time.time()
        with self._lock:
            self._minute_requests = [t for t in self._minute_requests if now - t < 60]
            self._day_requests = [t for t in self._day_requests if now - t < 86400]
            if len(self._minute_requests) >= self._max_rpm:
                return False
            if len(self._day_requests) >= self._max_rpd:
                return False
            return True

    def record_request(self):
        now = time.time()
        with self._lock:
            self._minute_requests.append(now)
            self._day_requests.append(now)

    @property
    def remaining_rpm(self) -> int:
        now = time.time()
        active = len([t for t in self._minute_requests if now - t < 60])
        return max(0, self._max_rpm - active)

    @property
    def remaining_rpd(self) -> int:
        now = time.time()
        active = len([t for t in self._day_requests if now - t < 86400])
        return max(0, self._max_rpd - active)


class AIProvider:
    """Single AI provider configuration and state."""

    def __init__(self, name: str, api_key: str, model: str, base_url: str,
                 max_rpm: int, max_rpd: int):
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = RateLimiter(max_rpm, max_rpd)
        self.consecutive_failures = 0
        self.last_failure_time = 0
        self.total_requests = 0
        self.total_failures = 0

    @property
    def available(self) -> bool:
        if self.consecutive_failures >= 3:
            cooldown = min(300, 30 * self.consecutive_failures)
            if time.time() - self.last_failure_time < cooldown:
                return False
            self.consecutive_failures = 0
        return self.rate_limiter.can_request()

    def record_success(self):
        self.consecutive_failures = 0
        self.total_requests += 1
        self.rate_limiter.record_request()

    def record_failure(self):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        self.total_failures += 1
        self.total_requests += 1
        self.rate_limiter.record_request()


class AIClient:
    """Unified AI client with automatic provider fallback."""

    def __init__(self, config: dict, http_client: HttpClient):
        self._logger = get_logger()
        self._http = http_client
        self._config = config
        self._providers: List[AIProvider] = []
        self._enabled = config.get("ai", {}).get("enabled", False)

        if not self._enabled:
            self._logger.info("AI features disabled in config")
            return

        for provider_cfg in config.get("ai", {}).get("providers", []):
            api_key = os.environ.get(provider_cfg.get("api_key_env", ""), "")
            if not api_key:
                self._logger.debug(f"AI provider '{provider_cfg['name']}' skipped - no API key")
                continue
            provider = AIProvider(
                name=provider_cfg["name"],
                api_key=api_key,
                model=provider_cfg["model"],
                base_url=provider_cfg["base_url"],
                max_rpm=provider_cfg.get("max_rpm", 30),
                max_rpd=provider_cfg.get("max_rpd", 14000),
            )
            self._providers.append(provider)
            self._logger.info(f"AI provider '{provider.name}' initialized (model: {provider.model})")

        if not self._providers:
            self._logger.warning("No AI providers available - AI features will be skipped")
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled and len(self._providers) > 0

    def chat(self, prompt: str, system_prompt: str = None, temperature: float = 0.3,
             max_tokens: int = 500, json_mode: bool = False) -> Optional[str]:
        if not self.enabled:
            return None
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        for provider in self._providers:
            if not provider.available:
                continue
            result = self._call_provider(provider, messages, temperature, max_tokens, json_mode)
            if result is not None:
                return result
        self._logger.warning("All AI providers exhausted for this request")
        return None

    def chat_json(self, prompt: str, system_prompt: str = None, temperature: float = 0.1,
                  max_tokens: int = 500) -> Optional[dict]:
        response = self.chat(prompt, system_prompt, temperature, max_tokens, json_mode=True)
        if not response:
            return None
        try:
            text = response.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except json.JSONDecodeError:
            try:
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(response[start:end])
            except json.JSONDecodeError:
                pass
            try:
                start = response.find("[")
                end = response.rfind("]") + 1
                if start >= 0 and end > start:
                    return json.loads(response[start:end])
            except json.JSONDecodeError:
                pass
            self._logger.warning(f"Failed to parse AI JSON response: {response[:200]}")
            return None

    def _call_provider(self, provider: AIProvider, messages: list,
                       temperature: float, max_tokens: int, json_mode: bool) -> Optional[str]:
        url = f"{provider.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = self._http.post(url, headers=headers, json=payload, delay=False, timeout=30)
            if resp is None:
                provider.record_failure()
                return None
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    provider.record_success()
                    return content
                provider.record_failure()
                return None
            elif resp.status_code == 429:
                self._logger.warning(f"AI provider '{provider.name}' rate limited")
                provider.record_failure()
                return None
            else:
                error_msg = ""
                try:
                    error_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    error_msg = resp.text[:200]
                self._logger.warning(f"AI provider '{provider.name}' error {resp.status_code}: {error_msg}")
                provider.record_failure()
                return None
        except Exception as e:
            self._logger.warning(f"AI provider '{provider.name}' exception: {e}")
            provider.record_failure()
            return None

    @property
    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "providers": [
                {
                    "name": p.name,
                    "available": p.available,
                    "total_requests": p.total_requests,
                    "total_failures": p.total_failures,
                    "remaining_rpm": p.rate_limiter.remaining_rpm,
                    "remaining_rpd": p.rate_limiter.remaining_rpd,
                }
                for p in self._providers
            ],
        }
