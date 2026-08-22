"""EcoBiz Copilot core logic.

Pipeline: load_data -> detect_anomalies -> calculate_impact

The detector needs no hardware: it compares consumption on non-working
days against the building's own "closed" baseline and flags days where
the building was clearly running as if it were occupied.
"""

import pandas as pd

TARIFF_KZT_PER_KWH = 17.447
CO2_KG_PER_KWH = 0.85
BASELINE_MULTIPLIER = 1.5


def load_data(file_path_or_buffer) -> pd.DataFrame:
    """Load daily consumption data from a CSV or Excel file."""
    path = str(file_path_or_buffer).lower()
    if path.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_path_or_buffer)
    return pd.read_csv(file_path_or_buffer, parse_dates=["date"])


def get_baseline(df: pd.DataFrame) -> float:
    """Closed-building baseline = 25th percentile of non-working-day consumption."""
    off_days = df.loc[df["is_workday"] == 0, "consumption_kwh"]
    if off_days.empty:
        raise ValueError("No non-working days found; cannot build a baseline.")
    return float(off_days.quantile(0.25))


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Flag non-working days whose consumption exceeds the closed-building baseline.

    Baseline = 25th percentile of non-working-day consumption, i.e. a day
    where everything was properly switched off. A robust statistic is used
    so the baseline is not inflated by the very waste we are detecting.
    A day is an anomaly when is_workday == 0 and
    consumption_kwh > baseline * BASELINE_MULTIPLIER.
    Adds boolean `is_anomaly` and `excess_kwh` (kWh above baseline, else 0).
    """
    df = df.copy()
    baseline = get_baseline(df)

    over_baseline = (df["is_workday"] == 0) & (
        df["consumption_kwh"] > baseline * BASELINE_MULTIPLIER
    )
    df["is_anomaly"] = over_baseline
    df["excess_kwh"] = (df["consumption_kwh"] - baseline).where(over_baseline, 0.0)
    return df


def calculate_impact(df: pd.DataFrame, tariff: float = TARIFF_KZT_PER_KWH) -> dict:
    """Sum up financial (KZT) and environmental (kg CO2) savings from anomalies.

    Requires detect_anomalies() to have run first.
    """
    total_excess = float(df["excess_kwh"].sum())
    return {
        "total_excess_kwh": round(total_excess, 2),
        "savings_kzt": round(total_excess * tariff, 2),
        "co2_saved_kg": round(total_excess * CO2_KG_PER_KWH, 2),
        "anomaly_days": int(df["is_anomaly"].sum()),
    }
