from datetime import datetime, timedelta

import pytest

from core.storage import (
    add_rule, add_usage, apply_pending, blocked_domains,
    get_status, init_db, request_remove_rule, set_limit,
)

T0 = datetime(2026, 9, 6, 10, 0)


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "test.db")


def test_zaostrzenie_limitu_dziala_natychmiast(conn):
    add_rule(conn, "youtube.com", 1200, now=T0)
    assert set_limit(conn, "youtube.com", 600, now=T0) is None
    assert get_status(conn, "youtube.com", "2026-09-06")["limit_seconds"] == 600


def test_poluzowanie_limitu_czeka_dobe(conn):
    add_rule(conn, "youtube.com", 1200, now=T0)
    effective = set_limit(conn, "youtube.com", 3600, now=T0)

    assert effective == T0 + timedelta(hours=24)
    assert get_status(conn, "youtube.com", "2026-09-06")["limit_seconds"] == 1200

    apply_pending(conn, now=T0 + timedelta(hours=23))
    assert get_status(conn, "youtube.com", "2026-09-06")["limit_seconds"] == 1200

    apply_pending(conn, now=T0 + timedelta(hours=25))
    assert get_status(conn, "youtube.com", "2026-09-06")["limit_seconds"] == 3600


def test_ponowne_zadanie_usuniecia_nie_przesuwa_terminu(conn):
    add_rule(conn, "x.com", 600, now=T0)
    first = request_remove_rule(conn, "x.com", now=T0)
    second = request_remove_rule(conn, "x.com", now=T0 + timedelta(hours=10))
    assert first == second


def test_ponowne_dodanie_anuluje_usuniecie(conn):
    add_rule(conn, "x.com", 600, now=T0)
    request_remove_rule(conn, "x.com", now=T0)
    add_rule(conn, "x.com", 600, now=T0 + timedelta(hours=1))

    apply_pending(conn, now=T0 + timedelta(hours=30))
    assert get_status(conn, "x.com", "2026-09-06")["tracked"] is True


def test_usage_kumuluje_i_blokuje(conn):
    add_rule(conn, "youtube.com", 60, now=T0)
    assert add_usage(conn, "youtube.com", "2026-09-06", 30) == 30
    assert add_usage(conn, "youtube.com", "2026-09-06", 25) == 55
    assert get_status(conn, "youtube.com", "2026-09-06")["blocked"] is False

    add_usage(conn, "youtube.com", "2026-09-06", 5)
    assert get_status(conn, "youtube.com", "2026-09-06")["blocked"] is True
    assert blocked_domains(conn, "2026-09-06") == ["youtube.com"]


def test_licznik_jest_per_dzien(conn):
    add_rule(conn, "youtube.com", 60, now=T0)
    add_usage(conn, "youtube.com", "2026-09-06", 100)
    assert get_status(conn, "youtube.com", "2026-09-07")["used_seconds"] == 0