"""
AI-powered hot word discovery - identifies emerging narratives from recent token data.
"""

import time
from typing import List, Optional

from ai.client import AIClient
from storage.database import Database
from infra.logger import get_logger

SYSTEM_PROMPT = """你是一名链上趋势分析师。从近期代币数据中提取新兴叙事关键词。

规则：
1. 只提取真正新兴的热词，忽略老meme（pepe、doge、shiba、wojak等）
2. 关键词应是具体可用于关键词匹配的词
3. 每个关键词需附带建议分类
4. 只返回高置信度的发现

只返回JSON格式，不要附加其他文字。"""

USER_PROMPT_TEMPLATE = """过去{hours}小时，出现了以下新代币：

{tokens_text}

提取新兴叙事关键词（最多5个）。

已知旧关键词（不要重复）：
{known_keywords}

返回JSON数组：
[
    {{
        "keyword": "关键词",
        "category": "musk_trump|binance_cz|celebrity_viral|general",
        "reason": "为什么是新趋势",
        "confidence": 0.0 到 1.0
    }}
]

如果没有新趋势，返回空数组 []"""


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
