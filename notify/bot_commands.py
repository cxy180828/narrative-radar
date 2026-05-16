"""
Telegram Bot command handler.
"""
import os, time, threading
from typing import Callable, Optional
from infra.http_client import HttpClient
from infra.logger import get_logger
from storage.database import Database


class BotCommandHandler:
    def __init__(self, http_client: HttpClient, db: Database, config: dict):
        self._http = http_client
        self._db = db
        self._logger = get_logger()
        tg_cfg = config.get("telegram", {})
        self._token = os.environ.get(tg_cfg.get("token_env", "TELEGRAM_BOT_TOKEN"), "")
        self._chat_id = os.environ.get(tg_cfg.get("chat_id_env", "TG_CHAT_ID"), "")
        self._enabled = tg_cfg.get("bot_commands", True) and bool(self._token)
        self._polling_interval = tg_cfg.get("polling_interval", 2)
        self._paused = False
        self._chain_filter = None
        self._last_update_id = 0
        self._running = False
        self._thread = None
        self._report_callback: Optional[Callable] = None
        self._ai_client = None  # Set via set_ai_client()
        # Admin whitelist - only these user IDs can execute commands
        admin_ids = os.environ.get("TG_ADMIN_IDS", "")
        self._admin_ids = set(admin_ids.split(",")) if admin_ids else set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def chain_filter(self) -> Optional[str]:
        return self._chain_filter

    def set_report_callback(self, callback: Callable):
        self._report_callback = callback

    def set_ai_client(self, ai_client):
        """Set AI client reference for /ai_status command."""
        self._ai_client = ai_client

    def start(self):
        if not self._enabled:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._logger.info("Bot command handler started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self):
        while self._running:
            try:
                self._process_updates()
            except Exception as e:
                self._logger.warning(f"Bot poll error: {e}")
            time.sleep(self._polling_interval)

    def _process_updates(self):
        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = {"offset": self._last_update_id + 1, "timeout": 1, "limit": 10}
        resp = self._http.get(url, params=params, delay=False, timeout=5)
        if not resp or resp.status_code != 200:
            return
        data = resp.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            update_id = update.get("update_id", 0)
            if update_id > self._last_update_id:
                self._last_update_id = update_id
            message = update.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(self._chat_id):
                continue
            if text.startswith("/"):
                user_id = str(message.get("from", {}).get("id", ""))
                self._handle_command(text, chat_id, user_id)
            callback = update.get("callback_query", {})
            if callback:
                cb_data = callback.get("data", "")
                self._handle_callback(cb_data, callback.get("id", ""))

    def _handle_command(self, text: str, chat_id: str, user_id: str = ""):
        # Admin check
        if self._admin_ids and user_id not in self._admin_ids:
            self._logger.warning(f"Unauthorized command from user {user_id}: {text}")
            return
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        if cmd == "/status":
            stats = self._db.get_daily_stats()
            self._reply(chat_id, f"Status: {'PAUSED' if self._paused else 'RUNNING'}\nFilter: {self._chain_filter or 'all'}\n24h: scanned={stats['scanned']} pushed={stats['pushed']}")
        elif cmd == "/pause":
            self._paused = True
            self._reply(chat_id, "Notifications paused.")
        elif cmd == "/resume":
            self._paused = False
            self._reply(chat_id, "Notifications resumed.")
        elif cmd == "/filter" and len(parts) > 1:
            chain = parts[1].lower()
            if chain in ["eth","bsc","sol","base"]:
                self._chain_filter = chain
                self._reply(chat_id, f"Filter: {chain.upper()} only")
            else:
                self._reply(chat_id, "Options: eth, bsc, sol, base")
        elif cmd == "/unfilter":
            self._chain_filter = None
            self._reply(chat_id, "Filter removed.")
        elif cmd == "/blacklist" and len(parts) > 1:
            self._db.add_to_blacklist(parts[1], reason="user_command")
            self._reply(chat_id, f"Blacklisted: {parts[1][:16]}...")
        elif cmd == "/addkw" and len(parts) >= 3:
            keyword = " ".join(parts[2:]).lower()
            self._db.add_hotword(keyword, parts[1], source="user")
            self._reply(chat_id, f"Added: '{keyword}' -> {parts[1]}")
        elif cmd == "/fp" and len(parts) > 1:
            self._db.record_false_positive(parts[1], "", "", "", reason="user_command")
            self._reply(chat_id, f"Marked FP: {parts[1][:16]}...")
        elif cmd == "/winrate":
            wr = self._db.get_win_rate(60, 7)
            self._reply(chat_id, f"7d win rate: {wr['win_rate']:.1f}% ({wr['wins']}/{wr['total']})")
        elif cmd == "/ai_status":
            if self._ai_client:
                self._reply(chat_id, self._ai_client.get_status_text())
            else:
                self._reply(chat_id, "AI client not available")
        elif cmd == "/report" and self._report_callback:
            self._report_callback()
            self._reply(chat_id, "Report triggered.")

    def _handle_callback(self, data: str, callback_id: str):
        url = f"https://api.telegram.org/bot{self._token}/answerCallbackQuery"
        self._http.post(url, json={"callback_query_id": callback_id}, delay=False)
        if data.startswith("fp:"):
            self._db.record_false_positive(data[3:], "", "", "", reason="button")
        elif data.startswith("bl:"):
            self._db.add_to_blacklist(data[3:], reason="button")

    def _reply(self, chat_id: str, text: str):
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        self._http.post(url, json={"chat_id": chat_id, "text": text}, delay=False, timeout=10)
