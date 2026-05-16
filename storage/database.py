"""
SQLite database management with WAL mode, batch operations, and auto-backup.
"""

import os
import shutil
import sqlite3
import time
from typing import List, Optional, Tuple

from infra.logger import get_logger


class Database:
    """SQLite database with WAL mode and batch operations."""

    def __init__(self, db_path: str, wal_mode: bool = True, busy_timeout: int = 5000):
        self._logger = get_logger()
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        if wal_mode:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._init_schema()
        self._logger.info(f"Database initialized: {db_path}")

    def _init_schema(self):
        """Create all tables and indexes."""
        c = self._conn.cursor()

        c.execute("""CREATE TABLE IF NOT EXISTS narratives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme TEXT NOT NULL UNIQUE,
            first_token_name TEXT,
            first_token_address TEXT,
            first_chain TEXT,
            first_seen_at INTEGER,
            token_count INTEGER DEFAULT 1,
            last_seen_at INTEGER
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS tokens_seen (
            address TEXT PRIMARY KEY,
            chain TEXT,
            name TEXT,
            symbol TEXT,
            narrative_theme TEXT,
            category TEXT,
            first_seen_at INTEGER,
            market_cap REAL,
            liquidity REAL DEFAULT 0,
            pushed INTEGER DEFAULT 0,
            seen_count INTEGER DEFAULT 1,
            ai_analysis TEXT,
            description_grade TEXT
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            chain TEXT,
            name TEXT,
            symbol TEXT,
            category TEXT,
            score INTEGER,
            market_cap_at_push REAL,
            price_at_push REAL,
            pushed_at INTEGER,
            narrative_tag TEXT,
            signal_count INTEGER DEFAULT 1
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS push_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            push_id INTEGER,
            address TEXT NOT NULL,
            chain TEXT,
            interval_minutes INTEGER,
            checked_at INTEGER,
            price_at_check REAL,
            market_cap_at_check REAL,
            pnl_pct REAL,
            FOREIGN KEY (push_id) REFERENCES push_history(id)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS hot_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            category TEXT,
            discovered_at INTEGER,
            source TEXT,
            active INTEGER DEFAULT 1
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS false_positives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            chain TEXT,
            name TEXT,
            symbol TEXT,
            marked_at INTEGER,
            reason TEXT
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS blacklist (
            address TEXT PRIMARY KEY,
            added_at INTEGER,
            reason TEXT
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS scan_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time INTEGER,
            tokens_found INTEGER,
            tokens_filtered INTEGER,
            momentum_signals INTEGER,
            pushed INTEGER,
            duration_ms INTEGER
        )""")

        c.execute("CREATE INDEX IF NOT EXISTS idx_narr_theme ON narratives(theme)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_token_addr ON tokens_seen(address)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_push_addr ON push_history(address)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_push_time ON push_history(pushed_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_perf_push ON push_performance(push_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_perf_addr ON push_performance(address)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_hotword ON hot_words(keyword)")

        self._conn.commit()

    def get_narrative(self, theme: str) -> Optional[dict]:
        c = self._conn.cursor()
        c.execute("SELECT * FROM narratives WHERE theme = ?", (theme,))
        row = c.fetchone()
        return dict(row) if row else None

    def get_recent_narratives(self, limit: int = 1000) -> List[dict]:
        c = self._conn.cursor()
        c.execute("SELECT * FROM narratives ORDER BY last_seen_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

    def upsert_narrative(self, theme: str, name: str, address: str, chain: str) -> Tuple[str, Optional[dict]]:
        now = int(time.time())
        existing = self.get_narrative(theme)
        if existing:
            new_count = existing["token_count"] + 1
            self._conn.execute(
                "UPDATE narratives SET token_count = ?, last_seen_at = ? WHERE theme = ?",
                (new_count, now, theme),
            )
            self._conn.commit()
            return "existing", existing
        self._conn.execute(
            "INSERT INTO narratives (theme, first_token_name, first_token_address, first_chain, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (theme, name, address, chain, now, now),
        )
        self._conn.commit()
        return "novel", None

    def increment_narrative_count(self, theme: str):
        now = int(time.time())
        self._conn.execute(
            "UPDATE narratives SET token_count = token_count + 1, last_seen_at = ? WHERE theme = ?",
            (now, theme),
        )
        self._conn.commit()

    def is_token_seen(self, address: str) -> bool:
        c = self._conn.cursor()
        c.execute("SELECT 1 FROM tokens_seen WHERE address = ?", (address,))
        return c.fetchone() is not None

    def record_token(self, address: str, chain: str, name: str, symbol: str,
                     theme: str, category: str, mc: float, liq: float = 0, pushed: bool = False):
        c = self._conn.cursor()
        c.execute("SELECT seen_count FROM tokens_seen WHERE address = ?", (address,))
        existing = c.fetchone()
        if existing:
            c.execute(
                "UPDATE tokens_seen SET seen_count = seen_count + 1, market_cap = ?, liquidity = ?, category = ? WHERE address = ?",
                (mc, liq, category, address),
            )
        else:
            c.execute(
                """INSERT INTO tokens_seen
                   (address, chain, name, symbol, narrative_theme, category, first_seen_at, market_cap, liquidity, pushed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (address, chain, name, symbol, theme, category, int(time.time()), mc, liq, 1 if pushed else 0),
            )
        self._conn.commit()

    def batch_record_tokens(self, tokens: List[dict]):
        now = int(time.time())
        c = self._conn.cursor()
        for t in tokens:
            c.execute("SELECT seen_count FROM tokens_seen WHERE address = ?", (t["address"],))
            existing = c.fetchone()
            if existing:
                c.execute(
                    "UPDATE tokens_seen SET seen_count = seen_count + 1, market_cap = ?, liquidity = ? WHERE address = ?",
                    (t.get("mc", 0), t.get("liq", 0), t["address"]),
                )
            else:
                c.execute(
                    """INSERT INTO tokens_seen
                       (address, chain, name, symbol, narrative_theme, category, first_seen_at, market_cap, liquidity, pushed)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                    (t["address"], t.get("chain", ""), t.get("name", ""), t.get("symbol", ""),
                     t.get("theme", ""), t.get("category", ""), now, t.get("mc", 0), t.get("liq", 0)),
                )
        self._conn.commit()

    def record_push(self, address: str, chain: str, name: str, symbol: str,
                    category: str, score: int, mc: float, price: float, narrative_tag: str, signal_count: int = 1) -> int:
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO push_history
               (address, chain, name, symbol, category, score, market_cap_at_push, price_at_push, pushed_at, narrative_tag, signal_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (address, chain, name, symbol, category, score, mc, price, int(time.time()), narrative_tag, signal_count),
        )
        self._conn.commit()
        return c.lastrowid

    def get_pending_performance_checks(self, intervals: List[int]) -> List[dict]:
        now = int(time.time())
        results = []
        c = self._conn.cursor()
        for interval_min in intervals:
            interval_sec = interval_min * 60
            c.execute("""
                SELECT ph.id, ph.address, ph.chain, ph.name, ph.price_at_push, ph.market_cap_at_push, ph.pushed_at
                FROM push_history ph
                WHERE ph.pushed_at <= ?
                AND ph.pushed_at >= ?
                AND NOT EXISTS (
                    SELECT 1 FROM push_performance pp
                    WHERE pp.push_id = ph.id AND pp.interval_minutes = ?
                )
            """, (now - interval_sec, now - interval_sec - 3600, interval_min))
            for row in c.fetchall():
                results.append({**dict(row), "interval_minutes": interval_min})
        return results

    def record_performance(self, push_id: int, address: str, chain: str,
                           interval_minutes: int, price: float, mc: float, pnl_pct: float):
        self._conn.execute(
            """INSERT INTO push_performance
               (push_id, address, chain, interval_minutes, checked_at, price_at_check, market_cap_at_check, pnl_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (push_id, address, chain, interval_minutes, int(time.time()), price, mc, pnl_pct),
        )
        self._conn.commit()

    def get_win_rate(self, interval_minutes: int = 60, lookback_days: int = 7) -> dict:
        cutoff = int(time.time()) - lookback_days * 86400
        c = self._conn.cursor()
        c.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl_pct >= 10 THEN 1 ELSE 0 END) as wins,
                   AVG(pnl_pct) as avg_pnl,
                   MAX(pnl_pct) as max_pnl,
                   MIN(pnl_pct) as min_pnl
            FROM push_performance pp
            JOIN push_history ph ON pp.push_id = ph.id
            WHERE pp.interval_minutes = ? AND ph.pushed_at >= ?
        """, (interval_minutes, cutoff))
        row = c.fetchone()
        if row and row["total"] > 0:
            return {
                "total": row["total"],
                "wins": row["wins"] or 0,
                "win_rate": ((row["wins"] or 0) / row["total"]) * 100,
                "avg_pnl": row["avg_pnl"] or 0,
                "max_pnl": row["max_pnl"] or 0,
                "min_pnl": row["min_pnl"] or 0,
            }
        return {"total": 0, "wins": 0, "win_rate": 0, "avg_pnl": 0, "max_pnl": 0, "min_pnl": 0}

    def get_daily_stats(self) -> dict:
        cutoff = int(time.time()) - 86400
        c = self._conn.cursor()
        c.execute("SELECT COUNT(*) FROM tokens_seen WHERE first_seen_at >= ?", (cutoff,))
        scanned = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM push_history WHERE pushed_at >= ?", (cutoff,))
        pushed = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM scan_stats WHERE scan_time >= ?", (cutoff,))
        rounds = c.fetchone()[0]
        return {"scanned": scanned, "pushed": pushed, "rounds": rounds}

    def record_scan_stats(self, tokens_found: int, tokens_filtered: int,
                          momentum_signals: int, pushed: int, duration_ms: int):
        self._conn.execute(
            "INSERT INTO scan_stats (scan_time, tokens_found, tokens_filtered, momentum_signals, pushed, duration_ms) VALUES (?, ?, ?, ?, ?, ?)",
            (int(time.time()), tokens_found, tokens_filtered, momentum_signals, pushed, duration_ms),
        )
        self._conn.commit()

    def get_active_hotwords(self) -> List[dict]:
        c = self._conn.cursor()
        c.execute("SELECT * FROM hot_words WHERE active = 1")
        return [dict(r) for r in c.fetchall()]

    def add_hotword(self, keyword: str, category: str, source: str = "ai"):
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO hot_words (keyword, category, discovered_at, source) VALUES (?, ?, ?, ?)",
                (keyword.lower(), category, int(time.time()), source),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            pass

    def is_blacklisted(self, address: str) -> bool:
        c = self._conn.cursor()
        c.execute("SELECT 1 FROM blacklist WHERE address = ?", (address,))
        return c.fetchone() is not None

    def add_to_blacklist(self, address: str, reason: str = ""):
        self._conn.execute(
            "INSERT OR IGNORE INTO blacklist (address, added_at, reason) VALUES (?, ?, ?)",
            (address, int(time.time()), reason),
        )
        self._conn.commit()

    def record_false_positive(self, address: str, chain: str, name: str, symbol: str, reason: str = ""):
        self._conn.execute(
            "INSERT INTO false_positives (address, chain, name, symbol, marked_at, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (address, chain, name, symbol, int(time.time()), reason),
        )
        self._conn.commit()

    def get_recent_false_positives(self, limit: int = 20) -> List[dict]:
        c = self._conn.cursor()
        c.execute("SELECT * FROM false_positives ORDER BY marked_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

    def get_false_positive_count(self) -> int:
        c = self._conn.cursor()
        c.execute("SELECT COUNT(*) FROM false_positives")
        return c.fetchone()[0]

    def backup(self, backup_dir: str = None):
        if not backup_dir:
            backup_dir = os.path.dirname(self._db_path)
        backup_file = os.path.join(backup_dir, f"radar_backup_{int(time.time())}.db")
        shutil.copy2(self._db_path, backup_file)
        self._logger.info(f"Database backed up to {backup_file}")
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("radar_backup_")],
            reverse=True,
        )
        for old in backups[7:]:
            os.remove(os.path.join(backup_dir, old))

    def close(self):
        self._conn.close()
