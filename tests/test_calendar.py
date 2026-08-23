"""Unit tests for the Kazakhstan workday calendar in calendar_utils.py."""

import pandas as pd

from calendar_utils import add_workday_column, load_off_periods_from_json
from core import DEFAULT_CALENDAR_PATH


def test_add_workday_column_flags_weekends_off():
    df = pd.DataFrame({"date": ["2026-02-02", "2026-02-07"], "consumption_kwh": [400, 80]})
    result = add_workday_column(df)
    assert result["is_workday"].tolist() == [1, 0]


def test_add_workday_column_respects_extra_off_period():
    df = pd.DataFrame({"date": ["2026-02-02", "2026-02-03"], "consumption_kwh": [100, 100]})
    result = add_workday_column(df, extra_off_periods=[("2026-02-02", "2026-02-02")])
    assert result["is_workday"].tolist() == [0, 1]


def test_add_workday_column_never_overwrites_a_manual_column():
    df = pd.DataFrame(
        {"date": ["2026-02-02", "2026-02-03"], "consumption_kwh": [100, 100], "is_workday": [1, 0]}
    )
    result = add_workday_column(df, extra_off_periods=[("2026-02-02", "2026-02-02")])
    assert result["is_workday"].tolist() == [1, 0]


def test_holidays_kz_json_loads_and_covers_a_known_school_break():
    periods = load_off_periods_from_json(DEFAULT_CALENDAR_PATH)
    assert periods, "holidays_kz.json should define at least one off period"
    df = pd.DataFrame({"date": ["2026-03-23"], "consumption_kwh": [50]})  # spring break 2026
    result = add_workday_column(df, extra_off_periods=periods)
    assert result["is_workday"].tolist() == [0]
