"""
Telegram push notification with rate control and markdown fallback.
"""
import os, time
from typing import Optional
from infra.http_client import HttpClient
from infra.logger import get_logger


class TelegramNotifier:
    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()
        tg_cfg = config.get("telegram", {})
        self._token = os.environ.get(tg_cfg.get("token_env", "TELEGRAM_BOT_TOKEN"), "")
        self._chat_id = os.environ.get(tg_cfg.get("chat_id_env", "TG_CHAT_ID"), "")
        self._enabled = bool(self._token and self._chat_id)
        self._send_count = 0
        self._last_send_time = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self._enabled:
            self._logger.debug(f"TG disabled, skip: {text[:60]}")
            return False
        elapsed = time.time() - self._last_send_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_send_time = time.time()
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
        resp = self._http.post(url, json=payload, delay=False, timeout=10)
        if resp and resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                self._send_count += 1
                return True
            if "can't parse" in result.get("description", "").lower():
                return self._send_plain(text)
        elif resp:
            try:
                if "can't parse" in resp.json().get("description", "").lower():
                    return self._send_plain(text)
            except Exception:
                pass
        return False

    def _send_plain(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        resp = self._http.post(url, json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True}, delay=False, timeout=10)
        if resp and resp.status_code == 200 and resp.json().get("ok"):
            self._send_count += 1
            return True
        return False

    def send_with_keyboard(self, text: str, buttons: list, parse_mode: str = "Markdown") -> bool:
        if not self._enabled:
            return False
        elapsed = time.time() - self._last_send_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_send_time = time.time()
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        keyboard = {"inline_keyboard": [buttons]}
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": parse_mode, "reply_markup": keyboard, "disable_web_page_preview": True}
        resp = self._http.post(url, json=payload, delay=False, timeout=10)
        if resp and resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                self._send_count += 1
                return True
            if "can't parse" in result.get("description", "").lower():
                payload.pop("parse_mode", None)
                resp2 = self._http.post(url, json=payload, delay=False, timeout=10)
                if resp2 and resp2.json().get("ok"):
                    self._send_count += 1
                    return True
        return False

    @property
    def stats(self) -> dict:
        return {"enabled": self._enabled, "messages_sent": self._send_count}
