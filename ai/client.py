"""
Unified LLM client with multi-provider fallback and rate limiting.

Supports ANY OpenAI-compatible API endpoint, including:
- Official APIs: Groq, DeepSeek, Gemini, OpenAI, Anthropic (via proxy)
- Relay/Proxy stations (中转站): one-api, new-api, chat-next-web, etc.
- Aggregators: OpenRouter, SiliconFlow, Together AI, Fireworks AI

Configuration is purely in config.yaml — just set base_url + model + api_key.
Providers are tried in order; if one fails/rate-limits, the next one is tried.
Supports priority groups for task-based routing.
"""

import json
import os
import time
import threading
from typing import Optional, List, Dict

from infra.logger import get_logger
from infra.http_client import HttpClient


class RateLimiter:
    """Token-bucket rate limiter per provider."""

    def __init__(self, max_rpm: int = 30, max_rpd: int = 14000):
        self._max_rpm = max_rpm
        self._max_rpd = max_rpd
        self._minute_requests: List[float] = []
        self._day_requests: List[float] = []
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
    """
    Single AI provider configuration and state.

    Supports any OpenAI-compatible endpoint:
    - Official: https://api.openai.com/v1
    - Groq: https://api.groq.com/openai/v1
    - DeepSeek: https://api.deepseek.com/v1
    - Relay (中转站): https://your-relay.com/v1
    - OpenRouter: https://openrouter.ai/api/v1
    - SiliconFlow: https://api.siliconflow.cn/v1
    - Together: https://api.together.xyz/v1
    - Fireworks: https://api.fireworks.ai/inference/v1
    - Local (Ollama): http://localhost:11434/v1
    """

    def __init__(self, name: str, api_key: str, model: str, base_url: str,
                 max_rpm: int, max_rpd: int, priority: int = 0,
                 timeout: int = 30, tags: List[str] = None,
                 extra_headers: Dict[str, str] = None,
                 supports_json_mode: bool = True):
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.priority = priority  # Lower = tried first within same group
        self.timeout = timeout
        self.tags = tags or []  # For task-based routing: ["fast", "cheap", "smart"]
        self.extra_headers = extra_headers or {}
        self.supports_json_mode = supports_json_mode
        self.rate_limiter = RateLimiter(max_rpm, max_rpd)
        self.consecutive_failures = 0
        self.last_failure_time = 0
        self.total_requests = 0
        self.total_failures = 0
        self.total_tokens_used = 0
        self.avg_latency_ms = 0

    @property
    def available(self) -> bool:
        """Check if provider is available. Pure read — does NOT mutate state."""
        if self.consecutive_failures >= 5:
            cooldown = min(600, 30 * (2 ** (self.consecutive_failures - 5)))
            if time.time() - self.last_failure_time < cooldown:
                return False
        elif self.consecutive_failures >= 3:
            cooldown = min(300, 30 * self.consecutive_failures)
            if time.time() - self.last_failure_time < cooldown:
                return False
        return self.rate_limiter.can_request()

    def try_recover(self):
        """Call before actually using a provider — resets backoff if cooldown expired."""
        if self.consecutive_failures >= 5:
            cooldown = min(600, 30 * (2 ** (self.consecutive_failures - 5)))
            if time.time() - self.last_failure_time >= cooldown:
                self.consecutive_failures = max(0, self.consecutive_failures - 2)
        elif self.consecutive_failures >= 3:
            cooldown = min(300, 30 * self.consecutive_failures)
            if time.time() - self.last_failure_time >= cooldown:
                self.consecutive_failures = 0

    def record_success(self, latency_ms: float = 0, tokens: int = 0):
        self.consecutive_failures = 0
        self.total_requests += 1
        self.total_tokens_used += tokens
        self.rate_limiter.record_request()
        if latency_ms > 0:
            if self.avg_latency_ms == 0:
                self.avg_latency_ms = latency_ms
            else:
                self.avg_latency_ms = self.avg_latency_ms * 0.8 + latency_ms * 0.2

    def record_failure(self):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        self.total_failures += 1
        self.total_requests += 1
        self.rate_limiter.record_request()

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 100.0
        return ((self.total_requests - self.total_failures) / self.total_requests) * 100


class AIClient:
    """
    Unified AI client with automatic multi-provider fallback.

    Features:
    - Priority-ordered provider list (tries providers in config order)
    - Automatic fallback on failure/rate-limit/timeout
    - Tag-based routing (send "fast" tasks to Groq, "smart" tasks to GPT-4, etc.)
    - Exponential backoff on consecutive failures
    - Supports any OpenAI-compatible API (official, relay/中转站, local)
    - Tracks latency and success rate per provider
    - TG command /ai_status to check provider health
    """

    def __init__(self, config: dict, http_client: HttpClient):
        self._logger = get_logger()
        self._http = http_client
        self._config = config
        self._providers: List[AIProvider] = []
        self._enabled = config.get("ai", {}).get("enabled", False)

        if not self._enabled:
            self._logger.info("AI features disabled in config")
            return

        # Initialize providers from config (order = priority)
        for idx, provider_cfg in enumerate(config.get("ai", {}).get("providers", [])):
            # Support both env var reference and direct api_key in config
            api_key = ""
            api_key_env = provider_cfg.get("api_key_env", "")
            if api_key_env:
                api_key = os.environ.get(api_key_env, "")
            # Also check direct api_key field (for relay stations that put key in config)
            if not api_key:
                api_key = provider_cfg.get("api_key", "")

            if not api_key:
                name = provider_cfg.get("name", f"provider_{idx}")
                self._logger.debug(f"AI provider '{name}' skipped - no API key")
                continue

            # Parse extra headers (useful for OpenRouter HTTP-Referer, etc.)
            extra_headers = provider_cfg.get("extra_headers", {})

            # Parse tags
            tags = provider_cfg.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]

            provider = AIProvider(
                name=provider_cfg.get("name", f"provider_{idx}"),
                api_key=api_key,
                model=provider_cfg["model"],
                base_url=provider_cfg["base_url"],
                max_rpm=provider_cfg.get("max_rpm", 60),
                max_rpd=provider_cfg.get("max_rpd", 100000),
                priority=provider_cfg.get("priority", idx),
                timeout=provider_cfg.get("timeout", 30),
                tags=tags,
                extra_headers=extra_headers,
                supports_json_mode=provider_cfg.get("supports_json_mode", True),
            )
            self._providers.append(provider)
            self._logger.info(
                f"AI provider '{provider.name}' initialized "
                f"(model: {provider.model}, base: {provider.base_url[:50]})"
            )

        # Sort by priority (lower = higher priority)
        self._providers.sort(key=lambda p: p.priority)

        if not self._providers:
            self._logger.warning("No AI providers available - AI features will be skipped")
            self._enabled = False
        else:
            self._logger.info(f"AI client ready: {len(self._providers)} providers loaded")

    @property
    def enabled(self) -> bool:
        return self._enabled and len(self._providers) > 0

    def chat(self, prompt: str, system_prompt: str = None, temperature: float = 0.3,
             max_tokens: int = 500, json_mode: bool = False,
             tags: List[str] = None) -> Optional[str]:
        """
        Send a chat completion request with automatic provider fallback.

        Args:
            prompt: User message
            system_prompt: Optional system message
            temperature: Sampling temperature
            max_tokens: Max response tokens
            json_mode: If True, request JSON output format
            tags: Optional tags to filter providers (e.g., ["fast"], ["smart"])
                  If specified, only providers with matching tags are tried first,
                  then remaining providers as fallback.

        Returns:
            Response text or None if all providers failed
        """
        if not self.enabled:
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Build provider order: tagged first, then rest
        providers = self._get_ordered_providers(tags)

        for provider in providers:
            if not provider.available:
                continue
            provider.try_recover()
            result = self._call_provider(provider, messages, temperature, max_tokens, json_mode)
            if result is not None:
                return result

        self._logger.warning("All AI providers exhausted for this request")
        return None

    def chat_json(self, prompt: str, system_prompt: str = None, temperature: float = 0.1,
                  max_tokens: int = 500, tags: List[str] = None) -> Optional[dict]:
        """Chat with JSON response parsing. Returns parsed dict/list or None."""
        response = self.chat(prompt, system_prompt, temperature, max_tokens,
                             json_mode=True, tags=tags)
        if not response:
            return None

        # Try to parse JSON from response
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
            # Try to find JSON object
            try:
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(response[start:end])
            except json.JSONDecodeError:
                pass
            # Try array
            try:
                start = response.find("[")
                end = response.rfind("]") + 1
                if start >= 0 and end > start:
                    return json.loads(response[start:end])
            except json.JSONDecodeError:
                pass

            self._logger.warning(f"Failed to parse AI JSON response: {response[:200]}")
            return None

    def _get_ordered_providers(self, tags: Optional[List[str]] = None) -> List[AIProvider]:
        """Get providers ordered by tag match then priority."""
        if not tags:
            return self._providers

        # Split into tagged matches and rest
        tagged = []
        rest = []
        for p in self._providers:
            if any(t in p.tags for t in tags):
                tagged.append(p)
            else:
                rest.append(p)

        return tagged + rest

    def _call_provider(self, provider: AIProvider, messages: list,
                       temperature: float, max_tokens: int, json_mode: bool) -> Optional[str]:
        """Make an API call to a specific provider. No HTTP-level retry — fallback is handled by the client."""
        import requests as _requests

        url = f"{provider.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        # Add any extra headers (e.g., OpenRouter requires HTTP-Referer)
        if provider.extra_headers:
            headers.update(provider.extra_headers)

        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Only add json_mode if provider supports it
        if json_mode and provider.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        start_time = time.time()
        try:
            # Use raw requests (no retry adapter) to avoid retry×rate-limiter mismatch
            resp = _requests.post(
                url, headers=headers, json=payload,
                timeout=provider.timeout,
            )

            latency_ms = (time.time() - start_time) * 1000

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    # Track token usage if available
                    usage = data.get("usage", {})
                    tokens = usage.get("total_tokens", 0)
                    provider.record_success(latency_ms, tokens)
                    return content
                provider.record_failure()
                return None

            elif resp.status_code == 429:
                self._logger.warning(f"AI '{provider.name}' rate limited (429)")
                provider.record_failure()
                return None

            elif resp.status_code == 401 or resp.status_code == 403:
                self._logger.error(f"AI '{provider.name}' auth failed ({resp.status_code}) - check API key")
                provider.record_failure()
                # Auth failures are permanent - set high failure count to skip for longer
                provider.consecutive_failures = 10
                provider.last_failure_time = time.time()
                return None

            elif resp.status_code >= 500:
                self._logger.warning(f"AI '{provider.name}' server error ({resp.status_code})")
                provider.record_failure()
                return None

            else:
                error_msg = ""
                try:
                    error_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    error_msg = resp.text[:200]
                self._logger.warning(f"AI '{provider.name}' error {resp.status_code}: {error_msg}")
                provider.record_failure()
                return None

        except Exception as e:
            self._logger.warning(f"AI '{provider.name}' exception: {e}")
            provider.record_failure()
            return None

    @property
    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "provider_count": len(self._providers),
            "providers": [
                {
                    "name": p.name,
                    "model": p.model,
                    "base_url": p.base_url[:60],
                    "available": p.available,
                    "priority": p.priority,
                    "tags": p.tags,
                    "total_requests": p.total_requests,
                    "total_failures": p.total_failures,
                    "success_rate": f"{p.success_rate:.1f}%",
                    "avg_latency_ms": f"{p.avg_latency_ms:.0f}",
                    "remaining_rpm": p.rate_limiter.remaining_rpm,
                    "remaining_rpd": p.rate_limiter.remaining_rpd,
                    "tokens_used": p.total_tokens_used,
                }
                for p in self._providers
            ],
        }

    def get_status_text(self) -> str:
        """Generate human-readable status for TG /ai_status command."""
        if not self._enabled:
            return "AI: DISABLED"
        lines = [f"AI Providers ({len(self._providers)}):"]
        for p in self._providers:
            status = "OK" if p.available else "DOWN"
            lines.append(
                f"  [{status}] {p.name} ({p.model})\n"
                f"       RPM: {p.rate_limiter.remaining_rpm}/{p.rate_limiter._max_rpm} | "
                f"Req: {p.total_requests} | Fail: {p.total_failures} | "
                f"Latency: {p.avg_latency_ms:.0f}ms"
            )
        return "\n".join(lines)
