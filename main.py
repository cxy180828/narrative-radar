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
from notify.formatter import format_momentum_alert, format_daily_report, format_startup_message, build_alert_buttons
from notify.bot_commands import BotCommandHandler


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
        self.bot_commands = BotCommandHandler(self.http, self.db, self.config)
        self.desc_cache = TTLCache(default_ttl=1800, max_size=3000)
        self.scan_count = 0
        self.total_pushed = 0
        self.last_backup_time = time.time()

    def run(self):
        self.logger.info("=" * 60)
        self.logger.info("Narrative Radar v2 starting...")
        self.logger.info("=" * 60)
        self.health.startup_check()
        if self.bot_commands.enabled:
            self.bot_commands.set_report_callback(self._trigger_report)
            self.bot_commands.start()
        self.telegram.send(format_startup_message(self.config))
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
        except Exception:
            pass
        if "sol" in self.config.get("scan", {}).get("chains", []):
            try:
                for t in self.pumpfun.fetch_new_tokens(limit=30):
                    if t["address"] not in seen:
                        seen.add(t["address"])
                        all_tokens.append(t)
            except Exception:
                pass
        if "bsc" in self.config.get("scan", {}).get("chains", []):
            try:
                for t in self.fourmeme.fetch_new_tokens(limit=20):
                    if t["address"] not in seen:
                        seen.add(t["address"])
                        all_tokens.append(t)
            except Exception:
                pass
        return all_tokens

    def _pre_filter(self, tokens: list) -> list:
        th = self.config.get("thresholds", {})
        min_mc, max_mc, min_liq = th.get("min_market_cap", 1000), th.get("max_market_cap", 10000000), th.get("min_liquidity", 500)
        min_age = th.get("min_age_minutes", 10) / 60
        return [t for t in tokens if not self.db.is_blacklisted(t["address"]) and min_mc <= (t.get("mc", 0) or 0) <= max_mc and (t.get("liq", 0) or 0) >= min_liq and (t.get("age_h", 999)) >= min_age]

    def _process_signals(self, signals: list) -> int:
        max_alerts = self.config.get("momentum", {}).get("max_alerts_per_round", 8)
        pushed = 0
        for signal in signals[:max_alerts * 2]:
            token = signal["token"]
            addr, chain = token["address"], token["chain"]
            safety = self.safety.check(chain, addr)
            if not safety.get("safe"):
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
                continue
            narrative_tag = self._build_narrative_tag(category, matched_kw, token, ai_result)
            ai_insight = None
            if push_level == "high" and self.copywriter.enabled:
                ai_insight = self.copywriter.enhance(name=token.get("name", ""), symbol=token.get("symbol", ""), chain=chain, mc=token.get("mc", 0), liq=token.get("liq", 0), narrative=narrative_tag, description=description, rounds=signal["rounds"], pct=signal["pct_gain"], score=score)
            msg = format_momentum_alert(token=token, pct_gain=signal["pct_gain"], rounds=signal["rounds"], vol_up=signal["vol_up"], score=score, narrative_tag=narrative_tag, desc_info=desc_info, signal_count=signal["signal_count"], ai_insight=ai_insight, push_level=push_level)
            buttons = build_alert_buttons(addr, chain)
            if self.telegram.send_with_keyboard(msg, buttons):
                pushed += 1
                self.db.record_push(addr, chain, token.get("name", ""), token.get("symbol", ""), category, score, token.get("mc", 0), token.get("price", 0), narrative_tag, signal["signal_count"])
                self.db.record_token(addr, chain, token.get("name", ""), token.get("symbol", ""), normalize_theme(token.get("name", ""), token.get("symbol", "")), category, token.get("mc", 0), token.get("liq", 0), pushed=True)
                self.logger.info(f"PUSHED [{push_level}]: {token.get('name')} score={score} +{signal['pct_gain']:.1f}%")
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
        if category == "musk_trump":
            return f"Musk/Trump ({', '.join((matched_kw or [])[:3])})"
        elif category == "binance_cz":
            return f"Binance/CZ ({', '.join((matched_kw or [])[:3])})"
        elif category == "celebrity_viral":
            return f"Celebrity ({', '.join((matched_kw or [])[:3])})"
        elif category == "new_narrative" and ai_result:
            return f"AI: {ai_result.get('narrative', 'new trend')}"
        theme = normalize_theme(token.get("name", ""), token.get("symbol", ""))
        words = [w for w in theme.split() if w not in COMMON_NOISE_WORDS and len(w) > 2]
        if len(words) >= 2:
            return f"Theme: {theme}"
        if token.get("launchpad"):
            return f"Launchpad: {token['launchpad']}"
        return "Momentum"

    def _periodic_tasks(self):
        now = time.time()
        if self.perf_tracker.should_check():
            try:
                self.perf_tracker.check_performance()
            except Exception:
                pass
        if self.hotword_discovery.should_run():
            try:
                recent = self._fetch_all_tokens()[:100]
                known = [hw["keyword"] for hw in self.db.get_active_hotwords()]
                discovered = self.hotword_discovery.discover(recent, known)
                if discovered:
                    self.telegram.send(f"New trends: {', '.join(d['keyword'] for d in discovered)}")
            except Exception:
                pass
        if self.ai_summary.should_run():
            self._trigger_report()
        if self.fp_learning.should_run():
            try:
                analysis = self.fp_learning.analyze()
                if analysis:
                    self.telegram.send(f"FP Analysis: {analysis.get('summary', 'done')}")
            except Exception:
                pass
        if self.calibrator.should_calibrate():
            try:
                self.calibrator.calibrate()
            except Exception:
                pass
        backup_interval = self.config.get("database", {}).get("backup_interval", 86400)
        if now - self.last_backup_time >= backup_interval:
            try:
                self.db.backup()
                self.last_backup_time = now
            except Exception:
                pass

    def _trigger_report(self):
        try:
            stats = self.db.get_daily_stats()
            win_rate = self.db.get_win_rate(60, 1)
            ai_text = self.ai_summary.generate_daily_summary() if self.ai_summary.enabled else None
            self.telegram.send(format_daily_report(stats, win_rate, ai_text))
        except Exception as e:
            self.logger.error(f"Report error: {e}")

    def _shutdown(self):
        self.logger.info("Shutting down...")
        if self.bot_commands.enabled:
            self.bot_commands.stop()
        self.telegram.send("Narrative Radar v2 shutting down.")
        self.db.close()
        self.logger.info("Shutdown complete.")


def main():
    RadarApp().run()


if __name__ == "__main__":
    main()
