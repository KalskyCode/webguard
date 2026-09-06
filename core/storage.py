"""Warstwa bazy. Jedyne miejsce w projekcie dotykające SQLite."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CHANGE_DELAY_HOURS = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    domain                TEXT PRIMARY KEY,
    limit_seconds         INTEGER NOT NULL CHECK (limit_seconds > 0),
    created_at            TEXT NOT NULL,
    pending_limit_seconds INTEGER,
    pending_effective_at  TEXT,
    remove_effective_at   TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    domain  TEXT NOT NULL,
    day     TEXT NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0 CHECK (seconds >= 0),
    PRIMARY KEY (domain, day)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _ts(dt: datetime) -> str:
    """Jeden format czasu w całej bazie. Sortuje się leksykograficznie."""
    return dt.replace(microsecond=0).isoformat(sep=" ")


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now()


# --- inicjalizacja ---------------------------------------------------------

def init_db(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)

    if get_meta(conn, "admin_token") is None:
        set_meta(conn, "admin_token", secrets.token_urlsafe(32))
    if get_meta(conn, "day_start_hour") is None:
        set_meta(conn, "day_start_hour", "4")

    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _get_rule(conn: sqlite3.Connection, domain: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rules WHERE domain = ?", (domain,)).fetchone()


# --- reguły ----------------------------------------------------------------

def add_rule(
    conn: sqlite3.Connection,
    domain: str,
    limit_seconds: int,
    now: datetime | None = None,
) -> None:
    """Dodaje regułę. Na istniejącej regule anuluje zaplanowane usunięcie
    i deleguje limit do set_limit (zaostrzenie natychmiast, poluzowanie za 24h).
    """
    if limit_seconds <= 0:
        raise ValueError("limit_seconds musi być dodatni")

    now = _now(now)
    if _get_rule(conn, domain) is not None:
        cancel_remove_rule(conn, domain)
        set_limit(conn, domain, limit_seconds, now)
        return

    conn.execute(
        "INSERT INTO rules(domain, limit_seconds, created_at) VALUES (?, ?, ?)",
        (domain, limit_seconds, _ts(now)),
    )


def set_limit(
    conn: sqlite3.Connection,
    domain: str,
    limit_seconds: int,
    now: datetime | None = None,
) -> datetime | None:
    """Zwraca None, gdy zmiana weszła natychmiast,
    albo datę, od której zacznie obowiązywać.
    """
    if limit_seconds <= 0:
        raise ValueError("limit_seconds musi być dodatni")

    rule = _get_rule(conn, domain)
    if rule is None:
        raise KeyError(f"brak reguły dla {domain}")

    now = _now(now)

    if limit_seconds <= rule["limit_seconds"]:
        # zaostrzenie: od razu, i kasujemy ewentualne oczekujące poluzowanie
        conn.execute(
            "UPDATE rules SET limit_seconds = ?, pending_limit_seconds = NULL, "
            "pending_effective_at = NULL WHERE domain = ?",
            (limit_seconds, domain),
        )
        return None

    effective = now + timedelta(hours=CHANGE_DELAY_HOURS)
    conn.execute(
        "UPDATE rules SET pending_limit_seconds = ?, pending_effective_at = ? "
        "WHERE domain = ?",
        (limit_seconds, _ts(effective), domain),
    )
    return effective


def request_remove_rule(
    conn: sqlite3.Connection, domain: str, now: datetime | None = None
) -> datetime:
    """Planuje usunięcie reguły. Ponowne wywołanie NIE przesuwa terminu."""
    rule = _get_rule(conn, domain)
    if rule is None:
        raise KeyError(f"brak reguły dla {domain}")

    if rule["remove_effective_at"]:
        return datetime.fromisoformat(rule["remove_effective_at"])

    effective = _now(now) + timedelta(hours=CHANGE_DELAY_HOURS)
    conn.execute(
        "UPDATE rules SET remove_effective_at = ? WHERE domain = ?",
        (_ts(effective), domain),
    )
    return effective


def cancel_remove_rule(conn: sqlite3.Connection, domain: str) -> None:
    """Anulowanie usunięcia to zaostrzenie, więc działa natychmiast."""
    conn.execute(
        "UPDATE rules SET remove_effective_at = NULL WHERE domain = ?", (domain,)
    )


def apply_pending(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    """Wywoływane przez demona w każdym cyklu. Materializuje dojrzałe zmiany."""
    ts = _ts(_now(now))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "DELETE FROM rules WHERE remove_effective_at IS NOT NULL "
            "AND remove_effective_at <= ?",
            (ts,),
        )
        conn.execute(
            "UPDATE rules SET limit_seconds = pending_limit_seconds, "
            "pending_limit_seconds = NULL, pending_effective_at = NULL "
            "WHERE pending_effective_at IS NOT NULL AND pending_effective_at <= ?",
            (ts,),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def list_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM rules ORDER BY domain")]


# --- zużycie czasu ---------------------------------------------------------

def add_usage(
    conn: sqlite3.Connection, domain: str, day: str, delta_seconds: int
) -> int:
    """Dolicza sekundy i zwraca nową sumę dla tej domeny tego dnia."""
    if delta_seconds < 0:
        raise ValueError("delta_seconds nie może być ujemna")

    row = conn.execute(
        "INSERT INTO usage(domain, day, seconds) VALUES (?, ?, ?) "
        "ON CONFLICT(domain, day) DO UPDATE SET seconds = seconds + excluded.seconds "
        "RETURNING seconds",
        (domain, day, delta_seconds),
    ).fetchone()
    return row["seconds"]


def get_status(conn: sqlite3.Connection, domain: str, day: str) -> dict[str, Any]:
    rule = _get_rule(conn, domain)
    used_row = conn.execute(
        "SELECT seconds FROM usage WHERE domain = ? AND day = ?", (domain, day)
    ).fetchone()
    used = used_row["seconds"] if used_row else 0

    if rule is None:
        return {
            "domain": domain,
            "tracked": False,
            "blocked": False,
            "limit_seconds": None,
            "used_seconds": used,
            "remaining_seconds": None,
        }

    limit = rule["limit_seconds"]
    return {
        "domain": domain,
        "tracked": True,
        "blocked": used >= limit,
        "limit_seconds": limit,
        "used_seconds": used,
        "remaining_seconds": max(0, limit - used),
        "pending_limit_seconds": rule["pending_limit_seconds"],
        "pending_effective_at": rule["pending_effective_at"],
        "remove_effective_at": rule["remove_effective_at"],
    }


def blocked_domains(conn: sqlite3.Connection, day: str) -> list[str]:
    """Lista domen do wpisania w plik hosts. Potrzebne w kroku 3."""
    rows = conn.execute(
        "SELECT r.domain FROM rules r "
        "LEFT JOIN usage u ON u.domain = r.domain AND u.day = ? "
        "WHERE COALESCE(u.seconds, 0) >= r.limit_seconds "
        "ORDER BY r.domain",
        (day,),
    )
    return [r["domain"] for r in rows]