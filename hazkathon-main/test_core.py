"""Минимальные тесты, которые команда может объяснить на технической защите."""

from __future__ import annotations

import io

import pandas as pd
import pytest

import advisor
from calendar_utils import add_workday_column
from core import calculate_impact, detect_anomalies, load_data


def test_detect_anomalies_finds_known_off_day_anomaly() -> None:
    # Нижний квартиль шести нерабочих дней равен 100 кВт·ч.
    # Поэтому 200 кВт·ч в нерабочий день — аномалия с избытком 100 кВт·ч.
    dataframe = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=10, freq="D"),
            "consumption_kwh": [450, 455, 100, 100, 100, 100, 200, 100, 440, 445],
            "is_workday": [1, 1, 0, 0, 0, 0, 0, 0, 1, 1],
        }
    )

    result = detect_anomalies(dataframe)

    assert result.loc[result["is_anomaly"], "date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-01-07"
    ]
    assert result.loc[result["is_anomaly"], "excess_kwh"].tolist() == [100.0]
    assert result.attrs["baseline_reliable"] is True


def test_calculate_impact_uses_given_tariff() -> None:
    dataframe = pd.DataFrame(
        {
            "is_anomaly": [True, False],
            "excess_kwh": [100.0, 0.0],
        }
    )

    impact = calculate_impact(dataframe, tariff=17.447)

    assert impact["total_excess_kwh"] == 100.0
    assert impact["savings_kzt"] == 1744.7
    assert impact["co2_saved_kg"] == 85.0
    assert impact["anomaly_days"] == 1


def test_detect_anomalies_reports_missing_consumption_column() -> None:
    dataframe = pd.DataFrame({"date": ["2026-01-01"], "is_workday": [0]})

    with pytest.raises(ValueError, match="consumption_kwh"):
        detect_anomalies(dataframe)


def test_advisor_returns_fallback_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    impact = {
        "total_excess_kwh": 100.0,
        "savings_kzt": 2812.0,
        "co2_saved_kg": 85.0,
        "anomaly_days": 1,
    }

    recommendation, source = advisor.generate_recommendation_with_source(
        impact,
        ["2026-01-07"],
        api_key=None,
    )

    assert recommendation
    assert "2 812,00" in recommendation
    assert source == "fallback"


def test_calendar_respects_extra_off_period_without_overwriting_manual_column() -> None:
    raw = pd.DataFrame(
        {
            "date": ["2026-02-02", "2026-02-03"],
            "consumption_kwh": [100, 100],
        }
    )
    result = add_workday_column(raw, [("2026-02-02", "2026-02-02")])
    assert result["is_workday"].tolist() == [0, 1]

    manual = raw.assign(is_workday=[1, 0])
    assert add_workday_column(manual)["is_workday"].tolist() == [1, 0]


def test_load_data_adds_workday_column_to_a_realistic_minimal_csv() -> None:
    raw_csv = io.StringIO("date,consumption_kwh\n2026-02-02,400\n2026-02-07,80\n")

    result = load_data(raw_csv, filename="school_export.csv")

    assert result["is_workday"].tolist() == [1, 0]
