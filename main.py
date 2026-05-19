#!/usr/bin/env python3
"""
Narrative Radar v2 - On-chain momentum scanner with AI enhancement.
"""

import os, sys, time
import yaml

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from infra.logger import setup_logger, get_logger
from infra.signals import ShutdownHandler
from infra.http_client import HttpClient
from infra.health import HealthChecker
from storage.database import Database
from storage.cache import TTLCache
from ai.client import AIClient
from ai.narrative import NarrativeAnalyzer, DescriptionGrader
from ai.hotwords import HotWordDiscovery
from ai.summary import AISummary, EnhancedCopywriter
from ai.learning import FalsePositiveLearning, ScoreCalibrator
from fetcher.gmgn import GmgnFetcher
from fetcher.dexscreener import DexScreenerFetcher
from fetcher.pumpfun import PumpFunFetcher
from fetcher.fourmeme import FourMemeFetcher
from engine.classifier import NarrativeClassifier, normalize_theme, COMMON_NOISE_WORDS
from engine.momentum import MomentumTracker
from engine.scorer import SignalScorer
from engine.safety import SafetyChecker
from engine.backtest import PerformanceTracker
from notify.telegram import TelegramNotifier
from notify.feishu import FeishuNotifier
from notify.formatter import format_momentum_alert, format_daily_report, format_startup_message, build_alert_buttons
from notify.bot_commands import BotCommandHandler
from paper_trading import PaperPortfolio, format_paper_daily_report


def load_config() -> dict:
    config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_env():
    env_files = [os.path.join(PROJECT_ROOT, ".env"), os.path.expanduser("~/.env")]
    for env_file in env_files:
        if not os.path.exists(env_file):
            continue
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v


class RadarApp:
    def __init__(self):
        load_env()
        self.config = load_config()
        log_cfg = self.config.get("logging", {})
        self.logger = setup_logger("radar", PROJECT_ROOT, log_cfg.get("level", "INFO"), log_cfg.get("max_bytes", 10485760), log_cfg.get("backup_count", 5), log_cfg.get("json_format", True))
        self.shutdown = ShutdownHandler()
        http_cfg = self.config.get("http", {})
        self.http = HttpClient(timeout=http_cfg.get("timeout", 15), retries=http_cfg.get("retries", 3), backoff_factor=http_cfg.get("backoff_factor", 1.0), status_forcelist=http_cfg.get("status_forcelist", [429, 500, 502, 503, 504]), ua_rotation=http_cfg.get("ua_rotation", True), random_delay_min=http_cfg.get("random_delay_min", 0.3), random_delay_max=http_cfg.get("random_delay_max", 1.5))
        self.health = HealthChecker(self.http, self.config)
        db_path = os.path.join(PROJECT_ROOT, "data", "radar.db")
        db_cfg = self.config.get("database", {})
        self.db = Database(db_path, db_cfg.get("wal_mode", True), db_cfg.get("busy_timeout", 5000))
        self.ai_client = AIClient(self.config, self.http)
        self.narrative_analyzer = NarrativeAnalyzer(self.ai_client, self.config)
        self.desc_grader = DescriptionGrader(self.ai_client, self.config)
        self.hotword_discovery = HotWordDiscovery(self.ai_client, self.db, self.config)
        self.ai_summary = AISummary(self.ai_client, self.db, self.config)
        self.copywriter = EnhancedCopywriter(self.ai_client, self.config)
        self.fp_learning = FalsePositiveLearning(self.ai_client, self.db, self.config)
        self.calibrator = ScoreCalibrator(self.db, self.config)
        self.gmgn = GmgnFetcher(self.http, self.config)
        self.dexscreener = DexScreenerFetcher(self.http, self.config)
        self.pumpfun = PumpFunFetcher(self.http, self.config)
        self.fourmeme = FourMemeFetcher(self.http, self.config)
        self.classifier = NarrativeClassifier(self.narrative_analyzer, self.desc_grader, self.db, self.config)
        self.momentum = MomentumTracker(self.config)
        self.scorer = SignalScorer(self.config)
        self.safety = SafetyChecker(self.http, self.config)
        self.perf_tracker = PerformanceTracker(self.gmgn, self.dexscreener, self.db, self.config)
        self.telegram = TelegramNotifier(self.http, self.config)
        self.feishu = FeishuNotifier(self.http, self.config)
        self.bot_commands = BotCommandHandler(self.http, self.db, self.config)
        # Paper trading simulator (off by default, opt-in via config.yaml)
        self.paper = PaperPortfolio(self.db, self.config, self.telegram, self.feishu)
        self.desc_cache = TTLCache(default_ttl=1800, max_size=3000)
        self.scan_count = 0
        self.total_pushed = 0
        self.last_backup_time = time.time()
        self._error_counts = {}  # {source: count} for monitoring

    def run(self):
        self.logger.info("=" * 60)
        self.logger.info("Narrative Radar v2 starting...")
        self.logger.info("=" * 60)
        self.health.startup_check()
        if self.bot_commands.enabled:
            self.bot_commands.set_report_callback(self._trigger_report)
            self.bot_commands.set_ai_client(self.ai_client)
            self.bot_commands.start()
        self._notify(format_startup_message(self.config))
        self.logger.info(f"Scan interval: {self.config.get('scan', {}).get('interval', 30)}s")
        self.logger.info(f"Chains: {self.config.get('scan', {}).get('chains', [])}")
        self.logger.info(f"AI: {'enabled' if self.ai_client.enabled else 'disabled'}")
        self.logger.info("Entering main scan loop...")
        while not self.shutdown.should_stop:
            try:
                self._scan_round()
            except Exception as e:
                self.logger.error(f"Scan round error: {e}", exc_info=True)
            self._periodic_tasks()
            interval = self.config.get("scan", {}).get("interval", 30)
            self.shutdown.wait(timeout=interval)
        self._shutdown()

    def _scan_round(self):
        start_time = time.time()
        self.scan_count += 1
        if self.bot_commands.is_paused:
            return
        tokens = self._fetch_all_tokens()
        tokens_found = len(tokens)
        if not tokens:
            if self.scan_count % 20 == 0:
                self.logger.info(f"Round {self.scan_count}: No tokens fetched")
            return
        chain_filter = self.bot_commands.chain_filter
        if chain_filter:
            tokens = [t for t in tokens if t.get("chain") == chain_filter]
        tokens = self._pre_filter(tokens)
        tokens_filtered = tokens_found - len(tokens)
        signals = self.momentum.update(tokens)
        pushed = self._process_signals(signals)
        # Paper trading: drive exit ladders & stop losses against the same
        # token snapshot the scanner already pulled this round (zero extra IO).
        try:
            self.paper.update_prices(tokens)
        except Exception as e:
            self.logger.warning(f"paper update_prices error: {e}")
        duration_ms = int((time.time() - start_time) * 1000)
        self.db.record_scan_stats(tokens_found, tokens_filtered, len(signals), pushed, duration_ms)
        self.total_pushed += pushed
        if pushed > 0:
            self.logger.info(f"Round {self.scan_count}: found={tokens_found} signals={len(signals)} pushed={pushed} total={self.total_pushed}")
        elif self.scan_count % 20 == 0:
            self.logger.info(f"Round {self.scan_count}: found={tokens_found} signals={len(signals)} no push")

    def _fetch_all_tokens(self) -> list:
        all_tokens, seen = [], set()
        try:
            for t in self.gmgn.fetch_new_tokens():
                if t["address"] not in seen:
                    seen.add(t["address"])
                    all_tokens.append(t)
        except Exception as e:
            self.logger.warning(f"GMGN error: {e}")
        try:
            for t in self.gmgn.fetch_flap_tokens():
                if t["address"] not in seen:
                    seen.add(t["address"])
                    all_tokens.append(t)
        except Exception as e:
            self.logger.warning(f"FLAP fetch error: {e}")
        if "sol" in self.config.get("scan", {}).get("chains", []):
            try:
                for t in self.pumpfun.fetch_new_tokens(limit=30):
                    if t["address"] not in seen:
                        seen.add(t["address"])
                        all_tokens.append(t)
            except Exception as e:
                self.logger.warning(f"pump.fun fetch error: {e}")
        if "bsc" in self.config.get("scan", {}).get("chains", []):
            try:
                for t in self.fourmeme.fetch_new_tokens(limit=20):
                    if t["address"] not in seen:
                        seen.add(t["address"])
                        all_tokens.append(t)
            except Exception as e:
                self.logger.warning(f"Four.Meme fetch error: {e}")
        return all_tokens

    def _pre_filter(self, tokens: list) -> list:
        """Apply hard filters and (every 20 rounds) log a breakdown of why
        candidates got rejected. The breakdown is the fastest way to spot
        misconfigured thresholds without re-running an external diag script.
        """
        th = self.config.get("thresholds", {})
        min_mc = th.get("min_market_cap", 1000)
        max_mc = th.get("max_market_cap", 10000000)
        min_liq = th.get("min_liquidity", 500)
        min_age = th.get("min_age_minutes", 10) / 60
        kept = []
        rejected = {"blacklisted": 0, "mc_too_low": 0, "mc_too_high": 0,
                    "liq_too_low": 0, "age_too_young": 0}
        for t in tokens:
            if self.db.is_blacklisted(t["address"]):
                rejected["blacklisted"] += 1
                continue
            mc = t.get("mc", 0) or 0
            liq = t.get("liq", 0) or 0
            age_h = t.get("age_h", 999) or 999
            if mc < min_mc:
                rejected["mc_too_low"] += 1
                continue
            if mc > max_mc:
                rejected["mc_too_high"] += 1
                continue
            if liq < min_liq:
                rejected["liq_too_low"] += 1
                continue
            if age_h < min_age:
                rejected["age_too_young"] += 1
                continue
            kept.append(t)
        if self.scan_count % 20 == 0:
            self.logger.info(
                f"[Filter] in={len(tokens)} kept={len(kept)} "
                + " ".join(f"{k}={v}" for k, v in rejected.items() if v)
            )
        return kept

    def _process_signals(self, signals: list) -> int:
        max_alerts = self.config.get("momentum", {}).get("max_alerts_per_round", 8)
        pushed = 0
        for signal in signals[:max_alerts * 2]:
            token = signal["token"]
            addr, chain = token["address"], token["chain"]
            safety = self.safety.check(chain, addr)
            if not safety.get("safe"):
                self.logger.debug(f"Safety blocked: {token.get('name')} ({chain}) - {safety.get('reason', '?')}")
                continue
            desc_info = self._get_description(chain, addr)
            description = (desc_info or {}).get("description", "")
            category, matched_kw, ai_result = self.classifier.classify(token.get("name", ""), token.get("symbol", ""), chain, description=description, market_cap=token.get("mc", 0))
            if category == "spam":
                continue
            desc_grade = None
            if desc_info:
                gr = self.classifier.grade_description(description, desc_info.get("twitter", ""), desc_info.get("telegram", ""), desc_info.get("website", ""))
                if gr:
                    desc_grade = gr.get("grade")
            decay = self.momentum.get_momentum_decay(addr)
            score = self.scorer.score(token=token, pct_gain=signal["pct_gain"], streak_rounds=signal["rounds"], signal_count=signal["signal_count"], vol_up=signal["vol_up"], category=category, desc_info=desc_info, desc_grade=desc_grade, momentum_decay=decay, ai_result=ai_result)
            push_level = self.scorer.get_push_level(score, self.config)
            if push_level == "low":
                self.logger.debug(f"Score too low: {token.get('name')} score={score} +{signal['pct_gain']:.1f}%")
                continue
            narrative_tag = self._build_narrative_tag(category, matched_kw, token, ai_result)
            ai_insight = None
            if push_level == "high" and self.copywriter.enabled:
                ai_insight = self.copywriter.enhance(name=token.get("name", ""), symbol=token.get("symbol", ""), chain=chain, mc=token.get("mc", 0), liq=token.get("liq", 0), narrative=narrative_tag, description=description, rounds=signal["rounds"], pct=signal["pct_gain"], score=score)
            msg = format_momentum_alert(token=token, pct_gain=signal["pct_gain"], rounds=signal["rounds"], vol_up=signal["vol_up"], score=score, narrative_tag=narrative_tag, desc_info=desc_info, signal_count=signal["signal_count"], ai_insight=ai_insight, push_level=push_level)
            buttons = build_alert_buttons(addr, chain)
            tg_sent = self.telegram.send_with_keyboard(msg, buttons)
            # 飞书卡片同样使用中文，与 TG 风格保持一致
            if self.feishu.enabled:
                dex_chain = {"sol": "solana", "eth": "ethereum", "bsc": "bsc", "base": "base"}.get(chain, chain)
                fs_buttons = [
                    {"text": "K线", "url": f"https://dexscreener.com/{dex_chain}/{addr}"},
                    {"text": "GMGN", "url": f"https://gmgn.ai/{chain}/token/{addr}"},
                ]
                vol_tag = " (放量)" if signal.get("vol_up") else ""
                fs_text = (
                    f"**评分:** {score}/100 | **链:** {chain.upper()}\n"
                    f"**连涨:** {signal['rounds']}轮 +{signal['pct_gain']:.1f}%{vol_tag}\n"
                    f"**市值:** ${token.get('mc', 0):,.0f} | **流动性:** ${token.get('liq', 0):,.0f}\n"
                    f"**叙事:** {narrative_tag}\n"
                )
                if ai_insight:
                    fs_text += f"**AI洞察:** {ai_insight}\n"
                fs_text += f"`{addr}`"
                self.feishu.send_card(
                    title=f"雷达信号: {token.get('name', '?')} ({token.get('symbol', '?')})",
                    text=fs_text,
                    buttons=fs_buttons,
                    color="green" if score >= 75 else "blue",
                )
            if tg_sent or self.feishu.enabled:
                pushed += 1
                push_id = self.db.record_push(addr, chain, token.get("name", ""), token.get("symbol", ""), category, score, token.get("mc", 0), token.get("price", 0), narrative_tag, signal["signal_count"])
                self.db.record_token(addr, chain, token.get("name", ""), token.get("symbol", ""), normalize_theme(token.get("name", ""), token.get("symbol", "")), category, token.get("mc", 0), token.get("liq", 0), pushed=True)
                self.logger.info(f"PUSHED [{push_level}]: {token.get('name')} score={score} +{signal['pct_gain']:.1f}%")
                # Paper trading: try to open a simulated position on the same token.
                # No-op when disabled, when min_score not met, when already holding,
                # at capacity, or when the daily circuit breaker is active.
                try:
                    self.paper.try_enter(token, score, push_id=push_id)
                except Exception as e:
                    self.logger.warning(f"paper try_enter error: {e}")
            if pushed >= max_alerts:
                break
        return pushed

    def _get_description(self, chain: str, address: str) -> dict:
        cache_key = f"{chain}:{address}"
        cached = self.desc_cache.get(cache_key)
        if cached is not None:
            return cached
        result = {"description": "", "twitter": "", "telegram": "", "website": ""}
        if chain in ("sol", "solana"):
            info = self.pumpfun.get_token_description(address)
            if info:
                result = info
                self.desc_cache.set(cache_key, result)
                return result
        info = self.dexscreener.get_token_info(address)
        if info:
            result = {"description": info.get("description", ""), "twitter": info.get("twitter", ""), "telegram": info.get("telegram", ""), "website": info.get("website", "")}
        self.desc_cache.set(cache_key, result)
        return result

    def _build_narrative_tag(self, category, matched_kw, token, ai_result):
        # 全部使用中文标签，与 Telegram / 飞书消息体保持一致
        if category == "musk_trump":
            return f"马斯克/川普 ({', '.join((matched_kw or [])[:3])})"
        elif category == "binance_cz":
            return f"币安/CZ ({', '.join((matched_kw or [])[:3])})"
        elif category == "celebrity_viral":
            return f"名人/热点 ({', '.join((matched_kw or [])[:3])})"
        elif category == "new_narrative" and ai_result:
            return f"AI叙事: {ai_result.get('narrative', '新趋势')}"
        theme = normalize_theme(token.get("name", ""), token.get("symbol", ""))
        words = [w for w in theme.split() if w not in COMMON_NOISE_WORDS and len(w) > 2]
        if len(words) >= 2:
            return f"主题: {theme}"
        if token.get("launchpad"):
            return f"平台: {token['launchpad']}"
        return "动量"

    def _periodic_tasks(self):
        now = time.time()
        if self.perf_tracker.should_check():
            try:
                self.perf_tracker.check_performance()
            except Exception as e:
                self.logger.warning(f"Performance check error: {e}")
        if self.hotword_discovery.should_run():
            try:
                recent = self._fetch_all_tokens()[:100]
                known = [hw["keyword"] for hw in self.db.get_active_hotwords()]
                discovered = self.hotword_discovery.discover(recent, known)
                if discovered:
                    self._notify(f"发现新热词: {', '.join(d['keyword'] for d in discovered)}")
            except Exception as e:
                self.logger.warning(f"Hotword discovery error: {e}")
        if self.ai_summary.should_run():
            self._trigger_report()
        if self.fp_learning.should_run():
            try:
                analysis = self.fp_learning.analyze()
                if analysis:
                    self._notify(f"误报分析: {analysis.get('summary', '完成')}")
            except Exception as e:
                self.logger.warning(f"FP learning error: {e}")
        if self.calibrator.should_calibrate():
            try:
                self.calibrator.calibrate()
            except Exception as e:
                self.logger.warning(f"Score calibration error: {e}")
        backup_interval = self.config.get("database", {}).get("backup_interval", 86400)
        if now - self.last_backup_time >= backup_interval:
            try:
                self.db.backup()
                self.last_backup_time = now
            except Exception as e:
                self.logger.warning(f"DB backup error: {e}")

    def _trigger_report(self):
        try:
            stats = self.db.get_daily_stats()
            win_rate = self.db.get_win_rate(60, 1)
            ai_text = self.ai_summary.generate_daily_summary() if self.ai_summary.enabled else None
            report = format_daily_report(stats, win_rate, ai_text)
            paper_section = format_paper_daily_report(self.db, self.config)
            if paper_section:
                report = report + "\n" + paper_section
            self._notify(report)
        except Exception as e:
            self.logger.error(f"Report error: {e}")

    def _shutdown(self):
        self.logger.info("Shutting down...")
        if self.bot_commands.enabled:
            self.bot_commands.stop()
        self._notify("链上雷达 v2 已停止运行。")
        self.db.close()
        self.logger.info("Shutdown complete.")

    def _record_error(self, source: str, error: Exception):
        """Record error for monitoring. Alert if threshold exceeded."""
        self._error_counts[source] = self._error_counts.get(source, 0) + 1
        self.logger.warning(f"[{source}] error #{self._error_counts[source]}: {error}")
        # Alert if any source has 10+ consecutive errors
        if self._error_counts[source] >= 10 and self._error_counts[source] % 10 == 0:
            self._notify(f"警告: {source} 累计 {self._error_counts[source]} 次错误")

    def _notify(self, text: str) -> bool:
        """Send message to all configured notification channels (TG + Feishu)."""
        sent = False
        if self.telegram.enabled:
            sent = self.telegram.send(text) or sent
        if self.feishu.enabled:
            sent = self.feishu.send(text) or sent
        return sent


def main():
    RadarApp().run()


if __name__ == "__main__":
    main()
