from datetime import datetime

from core.clock import current_day, next_day_boundary


def test_przed_granica_doby_to_poprzedni_dzien():
    # wtorek 02:00
    assert current_day(datetime(2026, 9, 8, 2, 0)) == "2026-09-07"


def test_dokladnie_na_granicy_to_nowy_dzien():
    assert current_day(datetime(2026, 9, 8, 4, 0)) == "2026-09-08"


def test_minute_przed_granica():
    assert current_day(datetime(2026, 9, 8, 3, 59)) == "2026-09-07"


def test_polnoc_nalezy_do_dnia_poprzedniego():
    assert current_day(datetime(2026, 9, 8, 0, 0)) == "2026-09-07"


def test_granica_o_polnocy_gdy_hour_zero():
    assert current_day(datetime(2026, 9, 8, 0, 0), day_start_hour=0) == "2026-09-08"


def test_next_boundary():
    assert next_day_boundary(datetime(2026, 9, 8, 2, 0)) == datetime(2026, 9, 8, 4, 0)
    assert next_day_boundary(datetime(2026, 9, 8, 5, 0)) == datetime(2026, 9, 9, 4, 0)