"""Generate one month of synthetic daily electricity data for EcoBiz Copilot.

Scenario: a school building. Workdays run high (~450 kWh). Weekends drop
to a "closed building" level (~80 kWh). During the winter break week the
building is empty (is_workday = 0) but consumption stays near workday
levels — equipment left on, that is the waste we want to detect.
"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START_DATE = "2025-12-01"
DAYS = 31
BREAK_START, BREAK_END = 15, 21

WORKDAY_KWH = 450.0
WEEKEND_KWH = 80.0
BREAK_KWH = 420.0
NOISE_STD = 25.0


def build_month() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    rows = []
    for date in pd.date_range(START_DATE, periods=DAYS, freq="D"):
        on_break = BREAK_START <= date.day <= BREAK_END
        weekday = date.weekday() < 5

        if weekday and not on_break:
            base, is_workday = WORKDAY_KWH, 1
        elif on_break:
            base, is_workday = BREAK_KWH, 0
        else:
            base, is_workday = WEEKEND_KWH, 0

        consumption = max(20.0, rng.normal(base, NOISE_STD))
        rows.append(
            {
                "date": date.date(),
                "consumption_kwh": round(consumption, 2),
                "is_workday": is_workday,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    out_path = Path(__file__).resolve().parent / "sample_data.csv"
    df = build_month()
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows -> {out_path}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
