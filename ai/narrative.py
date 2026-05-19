"""
AI-powered narrative analysis for tokens that keyword matching cannot classify.
"""

from typing import Optional

from ai.client import AIClient
from infra.logger import get_logger

SYSTEM_PROMPT = """你是一名链上叙事分析师。你的任务是判断新发行的代币是否属于热门叙事或趋势。

分类规则：
1. musk_trump - 与Musk、Tesla、SpaceX、Trump、MAGA等相关
2. binance_cz - 与Binance、CZ、何一、BNB Chain、PancakeSwap等相关
3. celebrity_viral - 与其他名人、病毒事件、流行meme、社会热点相关
4. new_narrative - 全新叙事/概念（说明是什么）
5. noise - 无意义随机名、垃圾币、仿盘

严格要求：
- 必须只返回JSON格式，不要附加其他文字。
- narrative 和 keywords 字段中的所有自然语言文本必须使用简体中文。
- 即便代币名是英文，也要把叙事概括为中文（例如："dogwifhat" -> "戴帽子的狗 meme"）。
- keywords 中可保留代币名/符号本身，但描述性词语必须中文。"""

USER_PROMPT_TEMPLATE = """分析以下代币：

代币名称：{name}
符号：{symbol}
链：{chain}
描述：{description}
市值：${market_cap:,.0f}

返回JSON：
{{
    "category": "musk_trump|binance_cz|celebrity_viral|new_narrative|noise",
    "narrative": "一句话描述该叙事（如果是noise则为空字符串）",
    "confidence": 0.0 到 1.0,
    "keywords": ["关键词1", "关键词2"]
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

    SYSTEM_PROMPT = """你是一名代币质量评估员。根据代币描述和社交媒体信息，判断项目的认真程度。

评级：
A = 有清晰的故事线/概念 + 活跃社区 + 社交媒体完善（至少2个渠道）
B = 有基本概念但社区不完善，或概念一般但有社交渠道
C = 描述无意义/无社交/明显机器生成的垃圾/仿盘

严格要求：
- 只返回JSON格式，不要任何附加文字。
- reason 字段必须使用简体中文，控制在30字以内。"""

    USER_PROMPT = """评估此代币：

描述：{description}
有Twitter：{has_twitter}
有TG群：{has_telegram}
有网站：{has_website}

返回JSON：
{{
    "grade": "A|B|C",
    "reason": "一句话理由"
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
