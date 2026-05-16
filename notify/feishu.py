"""
Feishu (飞书) webhook push notification.

Supports:
- Custom Bot webhook (自定义机器人)
- Rich card messages with buttons
- Rate control to avoid throttling
- Markdown-like formatting via Feishu rich text

Usage:
  Set FEISHU_WEBHOOK_URL in .env (or feishu.webhook_url_env in config.yaml)
  Optionally set FEISHU_WEBHOOK_SECRET for signed webhooks.
"""

import hashlib
import hmac
import base64
import os
import time
from typing import Optional

from infra.http_client import HttpClient
from infra.logger import get_logger


class FeishuNotifier:
    """Sends messages to Feishu via custom bot webhook."""

    def __init__(self, http_client: HttpClient, config: dict):
        self._http = http_client
        self._logger = get_logger()

        fs_cfg = config.get("feishu", {})
        self._webhook_url = os.environ.get(
            fs_cfg.get("webhook_url_env", "FEISHU_WEBHOOK_URL"), ""
        )
        self._secret = os.environ.get(
            fs_cfg.get("webhook_secret_env", "FEISHU_WEBHOOK_SECRET"), ""
        )

        self._enabled = bool(self._webhook_url)
        self._send_count = 0
        self._last_send_time = 0
        self._min_interval = 1.0  # Min 1s between messages

        if self._enabled:
            self._logger.info("Feishu notifier enabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, text: str) -> bool:
        """
        Send a plain text message to Feishu.
        """
        if not self._enabled:
            return False

        self._rate_wait()

        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }

        # Add signature if secret is configured
        self._add_sign(payload)

        return self._post(payload)

    def send_rich(self, title: str, content_lines: list) -> bool:
        """
        Send a rich text (post) message to Feishu.

        Args:
            title: Message title
            content_lines: List of lines, each line is a list of content elements.
                          Simple usage: [["text line 1"], ["text line 2"]]
                          Rich usage: [[{"tag": "text", "text": "hello "}], ...]
        """
        if not self._enabled:
            return False

        self._rate_wait()

        # Build rich text content
        zh_cn_content = []
        for line in content_lines:
            if isinstance(line, str):
                zh_cn_content.append([{"tag": "text", "text": line}])
            elif isinstance(line, list):
                # Already structured content
                processed = []
                for item in line:
                    if isinstance(item, str):
                        processed.append({"tag": "text", "text": item})
                    elif isinstance(item, dict):
                        processed.append(item)
                zh_cn_content.append(processed)

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": zh_cn_content,
                    }
                }
            },
        }

        self._add_sign(payload)
        return self._post(payload)

    def send_card(self, title: str, text: str, buttons: list = None, color: str = "blue") -> bool:
        """
        Send an interactive card message to Feishu.

        Args:
            title: Card title
            text: Card body text (supports markdown-like syntax)
            buttons: List of button dicts [{"text": "label", "url": "https://..."}]
            color: Card header color: blue/green/red/orange/purple
        """
        if not self._enabled:
            return False

        self._rate_wait()

        # Build card elements
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": text,
                },
            }
        ]

        # Add buttons if provided
        if buttons:
            actions = []
            for btn in buttons:
                action = {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": btn.get("text", "")},
                    "type": "primary",
                }
                if btn.get("url"):
                    action["url"] = btn["url"]
                actions.append(action)
            elements.append({"tag": "action", "actions": actions})

        # Color mapping
        color_map = {
            "blue": "blue",
            "green": "green",
            "red": "red",
            "orange": "orange",
            "purple": "purple",
        }

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color_map.get(color, "blue"),
                },
                "elements": elements,
            },
        }

        self._add_sign(payload)
        return self._post(payload)

    def _add_sign(self, payload: dict):
        """Add timestamp + signature to payload if secret is configured."""
        if not self._secret:
            return

        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self._secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), b"", hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")

        payload["timestamp"] = timestamp
        payload["sign"] = sign

    def _post(self, payload: dict) -> bool:
        """Post payload to webhook URL."""
        import requests as _requests

        try:
            resp = _requests.post(
                self._webhook_url,
                json=payload,
                timeout=10,
            )

            if resp and resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0 or data.get("StatusCode") == 0:
                    self._send_count += 1
                    return True
                else:
                    msg = data.get("msg", "") or data.get("StatusMessage", "")
                    self._logger.warning(f"Feishu send error: {msg}")
                    return False
            else:
                self._logger.warning(f"Feishu HTTP error: {resp.status_code if resp else 'no response'}")
                return False

        except Exception as e:
            self._logger.warning(f"Feishu send exception: {e}")
            return False

    def _rate_wait(self):
        """Enforce minimum interval between messages."""
        elapsed = time.time() - self._last_send_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_send_time = time.time()

    @property
    def stats(self) -> dict:
        return {"enabled": self._enabled, "messages_sent": self._send_count}
