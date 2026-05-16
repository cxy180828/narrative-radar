"""
Narrative classifier - keyword matching + AI semantic fallback.
"""

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from ai.narrative import NarrativeAnalyzer, DescriptionGrader
from storage.database import Database
from infra.logger import get_logger

MUSK_TRUMP_KEYWORDS = {"musk","elon","elonmusk","spacex","starship","tesla","cybertruck","neuralink","xai","grok","trump","donald","maga","potus","trump47","melania","barron","ivanka","dark maga","darkmaga","doge department","d.o.g.e","government efficiency","truth social","covfefe"}
MUSK_TRUMP_PATTERNS = [r"\belon\b",r"\bmusk\b",r"\btrump\b",r"\bmaga\b",r"\bspacex\b",r"\bstarship\b",r"\btesla\b",r"\bgrok\b",r"\bmelania\b",r"\bneuralink\b"]
BINANCE_CZ_KEYWORDS = {"cz","changpeng","zhao","binance","bnb","pancake","pancakeswap","giggle academy","yzi","yzi labs","fourmeme","four meme","4meme","heyi","yi he","he yi"}
BINANCE_CZ_PATTERNS = [r"\bcz\b",r"\bbinance\b",r"\bbnb\b",r"\bheyi\b",r"\bpancake\b",r"\byzi\b",r"\bfourmeme\b",r"\b4meme\b"]
CELEBRITY_VIRAL_KEYWORDS = {"vitalik","buterin","sam altman","satoshi","saylor","blackrock","coinbase","justin sun","etf","halving","mrbeast","snoop dogg","kanye","drake","nvidia","jensen huang"}
CELEBRITY_VIRAL_PATTERNS = [r"\bvitalik\b",r"\bsaylor\b",r"\bblackrock\b",r"\bcoinbase\b",r"\betf\b",r"\bhalving\b",r"\bmrbeast\b"]
SPAM_PATTERNS = [r"airdrop",r"presale",r"pre\s*sale",r"1000x",r"100x guaranteed",r"safe\s*moon",r"pornhub",r"porn",r"xxx",r"scam",r"rugpull",r"rug\s*pull",r"official\s*token",r"official\s*coin"]
COMMON_NOISE_WORDS = {"nice","good","bad","cool","hot","big","small","life","love","happy","sad","fun","lol","cat","dog","moon","sun","star","king","queen","gold","rich","cash","money","pump","dump","bull","bear","hello","world","yes","no","wow","omg","test","new","old","real","fake","coin","token","meme","pepe","wojak","the","and","for","from","with","this","that","peg","usd","usdt","usdc","dai"}


class NarrativeClassifier:
    def __init__(self, ai_analyzer: NarrativeAnalyzer, desc_grader: DescriptionGrader, db: Database, config: dict):
        self._ai = ai_analyzer
        self._grader = desc_grader
        self._db = db
        self._logger = get_logger()
        self._config = config
        self._dynamic_keywords = {}
        self._last_hotword_load = 0

    def classify(self, name: str, symbol: str, chain: str, description: str = "", market_cap: float = 0) -> Tuple[str, Optional[List[str]], Optional[dict]]:
        text = f"{name} {symbol}".lower()
        for pat in SPAM_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                return "spam", None, None
        matched = self._match_keywords(text, MUSK_TRUMP_KEYWORDS, MUSK_TRUMP_PATTERNS)
        if matched and chain.lower() in ("eth","ethereum","sol","solana","bsc","base"):
            return "musk_trump", matched, None
        matched = self._match_keywords(text, BINANCE_CZ_KEYWORDS, BINANCE_CZ_PATTERNS)
        if matched:
            if chain.lower() in ("bsc",):
                return "binance_cz", matched, None
            return "binance_cz_wrong_chain", matched, None
        matched = self._match_keywords(text, CELEBRITY_VIRAL_KEYWORDS, CELEBRITY_VIRAL_PATTERNS)
        if matched:
            return "celebrity_viral", matched, None
        self._refresh_hotwords()
        for category, keywords in self._dynamic_keywords.items():
            matched = [kw for kw in keywords if kw in text]
            if matched:
                return category, matched, None
        if self._ai.enabled and market_cap >= 5000:
            ai_result = self._ai.analyze(name, symbol, chain, description, market_cap)
            if ai_result and ai_result.get("confidence", 0) >= 0.7:
                ai_category = ai_result["category"]
                if ai_category != "noise":
                    return ai_category, ai_result.get("keywords", []), ai_result
        return "check_novelty", None, None

    def grade_description(self, description: str, twitter: str, telegram: str, website: str) -> Optional[dict]:
        if self._grader.enabled:
            return self._grader.grade(description, has_twitter=bool(twitter), has_telegram=bool(telegram), has_website=bool(website))
        score = 0
        if description and len(description) > 20: score += 1
        if twitter: score += 1
        if telegram: score += 1
        if website: score += 1
        if score >= 3: return {"grade": "A", "reason": "multiple social channels"}
        elif score >= 1: return {"grade": "B", "reason": "partial info"}
        return {"grade": "C", "reason": "no useful info"}

    def _match_keywords(self, text: str, keywords: set, patterns: list) -> List[str]:
        matched = [kw for kw in keywords if kw.lower() in text]
        if not matched:
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    matched.append(m.group())
        return matched if matched else []

    def _refresh_hotwords(self):
        import time
        now = time.time()
        if now - self._last_hotword_load < 300:
            return
        self._last_hotword_load = now
        self._dynamic_keywords.clear()
        hotwords = self._db.get_active_hotwords()
        for hw in hotwords:
            cat = hw.get("category", "general")
            self._dynamic_keywords.setdefault(cat, set()).add(hw["keyword"])


def normalize_theme(name: str, symbol: str) -> str:
    text = f"{name} {symbol}".lower().strip()
    noise = ["token","coin","inu","swap","finance","protocol","dao","defi","nft","meta","verse","fi","ai","pepe","wojak","chad","based"]
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\d+x?", "", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    words = [w for w in text.split() if w and len(w) > 1 and w not in noise]
    if not words:
        return name.lower().strip()
    return " ".join(sorted(set(words)))


def is_similar_theme(theme1: str, theme2: str, threshold: float = 0.7) -> bool:
    if theme1 == theme2:
        return True
    if theme1 in theme2 or theme2 in theme1:
        return True
    words1 = set(theme1.split())
    words2 = set(theme2.split())
    if words1 and words2:
        overlap = len(words1 & words2) / min(len(words1), len(words2))
        if overlap >= 0.6:
            return True
    return SequenceMatcher(None, theme1, theme2).ratio() >= threshold
