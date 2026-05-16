"""
Momentum tracker - detects consecutive price increases and generates signals.
"""

import time
from typing import Dict, List

from infra.logger import get_logger


class MomentumTracker:
    def __init__(self, config: dict):
        self._logger = get_logger()
        self._config = config.get("momentum", {})
        self._consecutive_up = self._config.get("consecutive_up", 3)
        self._min_pct_gain = self._config.get("min_pct_gain", 5.0)
        self._push_cooldown = self._config.get("push_cooldown", 300)
        self._max_snapshots = self._config.get("max_snapshots", 20)
        self._stale_timeout = self._config.get("stale_timeout", 600)
        self._tracker: Dict[str, List[dict]] = {}
        self._pushed: Dict[str, dict] = {}

    def update(self, tokens: List[dict]) -> List[dict]:
        now = time.time()
        signals = []
        current_addrs = set()
        for token in tokens:
            addr = token["address"]
            mc = token.get("mc", 0) or 0
            vol = token.get("volume", 0) or 0
            price = token.get("price", 0) or 0
            buys = token.get("buys_1h", 0) or token.get("buys", 0) or 0
            liq = token.get("liq", 0) or 0
            current_addrs.add(addr)
            if mc < 1000 or liq < 500 or mc > 10_000_000:
                continue
            self._tracker.setdefault(addr, [])
            snapshots = self._tracker[addr]
            if snapshots and snapshots[-1]["mc"] == mc and snapshots[-1]["vol"] == vol:
                continue
            snapshots.append({"ts": now, "mc": mc, "vol": vol, "price": price, "buys": buys})
            if len(snapshots) > self._max_snapshots:
                snapshots[:] = snapshots[-self._max_snapshots:]
            if len(snapshots) < self._consecutive_up:
                continue
            recent = snapshots[-self._consecutive_up:]
            is_consecutive_up = True
            for i in range(1, len(recent)):
                if recent[i-1]["mc"] <= 0 or recent[i]["mc"] <= recent[i-1]["mc"]:
                    is_consecutive_up = False
                    break
            if not is_consecutive_up:
                continue
            vol_increasing = all(recent[i]["buys"] >= recent[i-1]["buys"] * 0.8 for i in range(1, len(recent)))
            first_mc = recent[0]["mc"]
            last_mc = recent[-1]["mc"]
            pct_gain = ((last_mc - first_mc) / first_mc * 100) if first_mc > 0 else 0
            if pct_gain < self._min_pct_gain:
                continue
            push_info = self._pushed.get(addr, {"count": 0, "last_ts": 0, "last_mc": 0})
            if push_info["count"] > 0:
                if now - push_info["last_ts"] < self._push_cooldown:
                    continue
                if last_mc <= push_info["last_mc"]:
                    continue
            streak = self._count_up_streak(snapshots)
            push_info["count"] += 1
            push_info["last_ts"] = now
            push_info["last_mc"] = last_mc
            self._pushed[addr] = push_info
            signals.append({
                "token": token, "pct_gain": pct_gain, "rounds": streak,
                "vol_up": vol_increasing, "signal_count": push_info["count"],
            })
        self._cleanup(current_addrs, now)
        signals.sort(key=lambda x: x["pct_gain"], reverse=True)
        return signals

    def get_momentum_decay(self, address: str) -> float:
        snapshots = self._tracker.get(address, [])
        if len(snapshots) < 3:
            return 1.0
        gains = []
        for i in range(max(1, len(snapshots) - 5), len(snapshots)):
            prev_mc = snapshots[i-1]["mc"]
            curr_mc = snapshots[i]["mc"]
            if prev_mc > 0:
                gains.append((curr_mc - prev_mc) / prev_mc * 100)
        if len(gains) < 2:
            return 1.0
        decreasing_count = sum(1 for i in range(1, len(gains)) if gains[i] < gains[i-1])
        decay = 1.0 - (decreasing_count / len(gains))
        return max(0.2, decay)

    def _count_up_streak(self, snapshots: List[dict]) -> int:
        if not snapshots:
            return 0
        streak = 1
        for i in range(len(snapshots) - 1, 0, -1):
            if snapshots[i-1]["mc"] <= 0 or snapshots[i]["mc"] <= snapshots[i-1]["mc"]:
                break
            streak += 1
        return streak

    def _cleanup(self, current_addrs: set, now: float):
        stale = [a for a, s in self._tracker.items() if a not in current_addrs and s and now - s[-1]["ts"] > self._stale_timeout]
        for a in stale:
            del self._tracker[a]
        self._pushed = {k: v for k, v in self._pushed.items() if now - v.get("last_ts", 0) < 3600}

    @property
    def stats(self) -> dict:
        return {"tracked_tokens": len(self._tracker), "active_pushes": len(self._pushed)}
