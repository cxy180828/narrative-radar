"""
AI-powered hot word discovery - identifies emerging narratives from recent token data.
"""

import time
from typing import List, Optional

from ai.client import AIClient
from storage.database import Database
from infra.logger import get_logger

SYSTEM_PROMPT = """You are an on-chain trend analyst. Extract emerging narrative keywords from recent token data.

Rules:
1. Only extract genuinely new trending keywords, ignore old memes (pepe, doge, shiba, wojak etc)
2. Keywords should be specific words usable for keyword matching
3. Each keyword should have a suggested category
4. Only return high-confidence discoveries

Only return JSON format, no additional text."""

USER_PROMPT_TEMPLATE = """In the past {hours} hours, these new tokens appeared:

{tokens_text}

Extract emerging narrative keywords (max 5).

Known old keywords (do not repeat):
{known_keywords}

Return JSON array:
[
    {{
        "keyword": "keyword",
        "category": "musk_trump|binance_cz|celebrity_viral|general",
        "reason": "why this is a new trend",
        "confidence": 0.0 to 1.0
    }}
]

If no new trends found, return empty array []"""


class HotWordDiscovery:
    """Periodically analyzes recent tokens to discover new trending keywords."""

    def __init__(self, ai_client: AIClient, db: Database, config: dict):
        self._ai = ai_client
        self._db = db
        self._logger = get_logger()
        self._enabled = config.get("ai", {}).get("hotword_discovery", False)
        self._last_run = 0
        self._run_interval = 6 * 3600

    @property
    def enabled(self) -> bool:
        return self._enabled and self._ai.enabled

    def should_run(self) -> bool:
        return self.enabled and (time.time() - self._last_run >= self._run_interval)

    def discover(self, recent_tokens: List[dict], known_keywords: List[str]) -> List[dict]:
        if not self.enabled or not recent_tokens:
            return []
        self._last_run = time.time()
        tokens_text_lines = []
        for t in recent_tokens[:100]:
            tname = t.get("name", "?")
            tsym = t.get("symbol", "?")
            tchain = t.get("chain", "?")
            line = f"- {tname} ({tsym}) [{tchain}]"
            desc = t.get("description", "")
            if desc:
                line += f" - {desc[:80]}"
            tokens_text_lines.append(line)
        tokens_text = "\n".join(tokens_text_lines)
        known_text = ", ".join(known_keywords[:50]) if known_keywords else "none"
        prompt = USER_PROMPT_TEMPLATE.format(
            hours=6, tokens_text=tokens_text, known_keywords=known_text,
        )
        result = self._ai.chat_json(prompt, system_prompt=SYSTEM_PROMPT, temperature=0.2, max_tokens=400)
        if result and isinstance(result, list):
            valid_results = []
            for item in result:
                if isinstance(item, dict) and "keyword" in item and "category" in item:
                    confidence = item.get("confidence", 0)
                    if confidence >= 0.6:
                        keyword = item["keyword"].lower().strip()
                        if keyword and len(keyword) >= 2 and keyword not in known_keywords:
                            valid_results.append(item)
                            self._db.add_hotword(keyword, item["category"], source="ai")
                            cat = item["category"]
                            self._logger.info(f"New hot word: '{keyword}' (category: {cat})")
            return valid_results
        return []
