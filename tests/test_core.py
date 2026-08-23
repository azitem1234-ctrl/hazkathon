"""Unit tests for the anomaly-detection engine in core.py."""

import io

import pytest
import pandas as pd

from core import (
    BASELINE_MULTIPLIER,
    MIN_OFF_DAY_SAMPLES,
    calculate_impact,
    detect_anomalies,
    get_baseline,
    load_data,
)


@pytest.fixture()
def sample_df():
    return load_data("data/sample_data.csv")


def test_load_data_shapes(sample_df):
    assert list(sample_df.columns) == ["date", "consumption_kwh", "is_workday"]
    assert len(sample_df) == 31


def test_baseline_is_quartile_of_closed_days(sample_df):
    expected = sample_df.loc[sample_df["is_workday"] == 0, "consumption_kwh"].quantile(0.25)
    assert get_baseline(sample_df) == pytest.approx(expected)


def test_baseline_raises_without_closed_days():
    df = pd.DataFrame(
        {"date": pd.date_range("2026-01-01", periods=3), "consumption_kwh": [10.0, 20.0, 30.0], "is_workday": [1, 1, 1]}
    )
    with pytest.raises(ValueError, match="non-working days"):
        get_baseline(df)


def test_detects_exactly_the_break_week(sample_df):
    flagged = detect_anomalies(sample_df)
    anomalies = flagged[flagged["is_anomaly"]]
    assert len(anomalies) == 7
    assert all(anomalies["date"].dt.day.between(15, 21))
    assert not any(flagged.loc[~flagged.index.isin(anomalies.index), "is_anomaly"])


def test_normal_weekend_is_not_flagged(sample_df):
    flagged = detect_anomalies(sample_df)
    weekends = flagged[(flagged["is_workday"] == 0) & (~flagged["date"].dt.day.between(15, 21))]
    assert not weekends["is_anomaly"].any()
    assert (weekends["excess_kwh"] == 0).all()


def test_threshold_rule_matches_multiplier(sample_df):
    baseline = get_baseline(sample_df) * BASELINE_MULTIPLIER
    flagged = detect_anomalies(sample_df)
    manual = (flagged["is_workday"] == 0) & (flagged["consumption_kwh"] > baseline)
    assert (manual == flagged["is_anomaly"]).all()


def test_impact_math(sample_df):
    tariff = 17.447
    flagged = detect_anomalies(sample_df)
    impact = calculate_impact(flagged, tariff=tariff)

    excess = flagged["excess_kwh"].sum()
    assert impact["total_excess_kwh"] == pytest.approx(round(excess, 2))
    assert impact["savings_kzt"] == pytest.approx(round(excess * tariff, 2))
    assert impact["co2_saved_kg"] == pytest.approx(round(excess * 0.85, 2), rel=1e-9)
    assert impact["anomaly_days"] == 7


def test_impact_scales_with_tariff(sample_df):
    flagged = detect_anomalies(sample_df)
    low = calculate_impact(flagged, tariff=10)
    high = calculate_impact(flagged, tariff=100)
    assert high["savings_kzt"] == pytest.approx(low["savings_kzt"] * 10)


def test_impact_accepts_custom_co2_factor(sample_df):
    flagged = detect_anomalies(sample_df)
    impact = calculate_impact(flagged, tariff=10, co2_kg_per_kwh=1.0)
    assert impact["co2_saved_kg"] == pytest.approx(impact["total_excess_kwh"])


def test_baseline_reliable_true_on_the_full_sample(sample_df):
    flagged = detect_anomalies(sample_df)
    assert flagged.attrs["baseline_reliable"] is True
    assert flagged.attrs["off_day_samples"] >= MIN_OFF_DAY_SAMPLES


def test_baseline_reliable_false_with_too_few_off_days():
    off_days = MIN_OFF_DAY_SAMPLES - 1
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=off_days + 5),
            "consumption_kwh": [100.0] * off_days + [400.0] * 5,
            "is_workday": [0] * off_days + [1] * 5,
        }
    )
    flagged = detect_anomalies(df)
    assert flagged.attrs["baseline_reliable"] is False
    assert flagged.attrs["off_day_samples"] == off_days


def test_load_data_derives_is_workday_from_kz_calendar_when_missing():
    """A raw export with only date/consumption_kwh: weekdays default to workday,
    weekends and holidays_kz.json periods default to non-working."""
    raw_csv = io.StringIO("date,consumption_kwh\n2026-02-02,400\n2026-02-07,80\n")
    result = load_data(raw_csv, filename="school_export.csv")
    assert result["is_workday"].tolist() == [1, 0]


def test_load_data_keeps_manual_is_workday_column(sample_df):
    """A school-supplied schedule always wins over the calendar guess."""
    assert list(sample_df.columns) == ["date", "consumption_kwh", "is_workday"]
