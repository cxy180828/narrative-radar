"""
AI-powered narrative analysis for tokens that keyword matching cannot classify.
"""

from typing import Optional

from ai.client import AIClient
from infra.logger import get_logger

SYSTEM_PROMPT = """You are an on-chain narrative analyst. Your task is to determine if a newly issued token belongs to a popular narrative or trend.

Classification rules:
1. musk_trump - Related to Musk, Tesla, SpaceX, Trump, MAGA etc.
2. binance_cz - Related to Binance, CZ, Yi He, BNB Chain, PancakeSwap etc.
3. celebrity_viral - Related to other celebrities, viral events, popular memes, social hot topics
4. new_narrative - A completely new narrative/concept never seen before (explain what it is)
5. noise - Meaningless random name, junk coin, copycat

You must ONLY return JSON format, no additional text."""

USER_PROMPT_TEMPLATE = """Analyze the following token:

Token name: {name}
Symbol: {symbol}
Chain: {chain}
Description: {description}
Market cap: ${market_cap:,.0f}

Return JSON:
{{
    "category": "musk_trump|binance_cz|celebrity_viral|new_narrative|noise",
    "narrative": "one sentence describing the narrative (empty string if noise)",
    "confidence": 0.0 to 1.0,
    "keywords": ["keyword1", "keyword2"]
}}"""


class NarrativeAnalyzer:
    """AI-powered narrative classification for ambiguous tokens."""

    def __init__(self, ai_client: AIClient, config: dict):
        self._ai = ai_client
        self._logger = get_logger()
        self._enabled = config.get("ai", {}).get("narrative_analysis", False)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._ai.enabled

    def analyze(self, name: str, symbol: str, chain: str,
                description: str = "", market_cap: float = 0) -> Optional[dict]:
        if not self.enabled:
            return None
        prompt = USER_PROMPT_TEMPLATE.format(
            name=name, symbol=symbol, chain=chain,
            description=description[:500] if description else "none",
            market_cap=market_cap,
        )
        result = self._ai.chat_json(prompt, system_prompt=SYSTEM_PROMPT, temperature=0.1, max_tokens=200)
        if result and isinstance(result, dict):
            if "category" in result and "confidence" in result:
                category = result.get("category", "noise")
                valid_categories = {"musk_trump", "binance_cz", "celebrity_viral", "new_narrative", "noise"}
                if category not in valid_categories:
                    category = "noise"
                result["category"] = category
                conf = result.get("confidence", 0)
                self._logger.debug(
                    f"AI narrative: {name} ({symbol}) -> {category} "
                    f"(confidence: {conf:.2f})"
                )
                return result
        return None


class DescriptionGrader:
    """AI-powered token description quality assessment."""

    SYSTEM_PROMPT = """You are a token quality assessor. Based on the token description and social media info, judge the project seriousness.

Grades:
A = Clear storyline/concept + active community + social media complete (at least 2 channels)
B = Basic concept but incomplete community, or average concept with social channels
C = Meaningless description/no socials/obviously machine-generated junk/copycat

Only return JSON format."""

    USER_PROMPT = """Assess this token:

Description: {description}
Has Twitter: {has_twitter}
Has TG group: {has_telegram}
Has Website: {has_website}

Return JSON:
{{
    "grade": "A|B|C",
    "reason": "one sentence reason"
}}"""

    def __init__(self, ai_client: AIClient, config: dict):
        self._ai = ai_client
        self._logger = get_logger()
        self._enabled = config.get("ai", {}).get("description_grading", False)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._ai.enabled

    def grade(self, description: str, has_twitter: bool, has_telegram: bool, has_website: bool) -> Optional[dict]:
        if not self.enabled:
            return None
        if not description and not has_twitter and not has_telegram and not has_website:
            return {"grade": "C", "reason": "no info available"}
        prompt = self.USER_PROMPT.format(
            description=description[:300] if description else "none",
            has_twitter="yes" if has_twitter else "no",
            has_telegram="yes" if has_telegram else "no",
            has_website="yes" if has_website else "no",
        )
        result = self._ai.chat_json(prompt, system_prompt=self.SYSTEM_PROMPT, temperature=0.1, max_tokens=100)
        if result and isinstance(result, dict) and "grade" in result:
            grade = result["grade"].upper()
            if grade not in ("A", "B", "C"):
                grade = "B"
            result["grade"] = grade
            return result
        return None
