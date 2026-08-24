"""API contract tests for the EcoBiz Copilot endpoints."""

import io

from core import load_data


def _post_csv(client, csv_text: str, **params):
    return client.post(
        "/api/analyze",
        params=params,
        files={"file": ("upload.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_analyze_sample_without_file(client):
    res = client.post("/api/analyze")
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "sample_data.csv"
    assert body["summary"]["anomaly_days"] == 7
    assert len(body["series"]) == 31
    assert body["worst_day"] is not None
    assert body["first_anomaly"] == "2025-12-15"
    assert body["last_anomaly"] == "2025-12-21"
    assert body["summary"]["baseline_reliable"] is True


def test_analyze_without_is_workday_uses_kz_calendar(client):
    """A raw export with only date/consumption_kwh still works: weekends and
    the Kazakhstan holiday calendar (holidays_kz.json) fill in is_workday."""
    res = _post_csv(
        client,
        "date,consumption_kwh\n"
        "2026-02-02,400\n"  # Monday -> workday
        "2026-02-07,80\n",  # Saturday -> non-working
    )
    assert res.status_code == 200
    series = {row["date"]: row for row in res.json()["series"]}
    assert series["2026-02-02"]["is_workday"] is True
    assert series["2026-02-07"]["is_workday"] is False
    # Only one non-working day in this tiny sample: too thin to trust.
    assert res.json()["summary"]["baseline_reliable"] is False
    assert res.json()["summary"]["off_day_samples"] == 1


def test_upload_csv_matches_core_pipeline(client, sample_csv_bytes):
    expected = len(load_data(io.BytesIO(sample_csv_bytes)))
    res = client.post(
        "/api/analyze", files={"file": ("data.csv", io.BytesIO(sample_csv_bytes), "text/csv")}
    )
    assert res.status_code == 200
    assert len(res.json()["series"]) == expected
    assert res.json()["source"] == "data.csv"


def test_tariff_changes_money_but_not_kwh(client):
    low = client.post("/api/analyze", params={"tariff": 10}).json()
    high = client.post("/api/analyze", params={"tariff": 100}).json()
    assert high["summary"]["savings_kzt"] == low["summary"]["savings_kzt"] * 10
    assert high["summary"]["total_excess_kwh"] == low["summary"]["total_excess_kwh"]
    assert high["summary"]["anomaly_days"] == low["summary"]["anomaly_days"]


def test_kwh_column_alias_is_accepted(client):
    """Real-world exports sometimes call the reading column 'kwh' instead of
    'consumption_kwh' — that should still work, not 422."""
    res = _post_csv(
        client,
        "date,kwh,is_workday\n"
        "2026-02-02,400,1\n"
        "2026-02-07,80,0\n",
    )
    assert res.status_code == 200
    series = {row["date"]: row for row in res.json()["series"]}
    assert series["2026-02-02"]["consumption_kwh"] == 400.0
    assert series["2026-02-07"]["consumption_kwh"] == 80.0


def test_multiplier_changes_anomaly_count(client):
    loose = client.post("/api/analyze", params={"multiplier": 1.2}).json()
    default = client.post("/api/analyze", params={"multiplier": 1.5}).json()
    assert loose["summary"]["multiplier"] == 1.2
    assert default["summary"]["multiplier"] == 1.5
    assert loose["summary"]["anomaly_days"] >= default["summary"]["anomaly_days"]


def test_zero_multiplier_rejected(client):
    assert client.post("/api/analyze", params={"multiplier": 0}).status_code == 422


def test_missing_columns_returns_422(client):
    res = _post_csv(client, "a,b,c\n1,2,3\n")
    assert res.status_code == 422
    assert "Отсутствуют обязательные колонки" in res.json()["detail"]


def test_bad_values_return_422(client):
    res = _post_csv(client, "date,consumption_kwh,is_workday\nnope,not-a-number,1\n")
    assert res.status_code == 422
    assert "Некорректные значения" in res.json()["detail"]


def test_empty_rows_return_422(client):
    res = _post_csv(client, "date,consumption_kwh,is_workday\n")
    assert res.status_code == 422


def test_wrong_extension_returns_415(client):
    res = client.post("/api/analyze", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 415


def test_negative_tariff_rejected_on_sample(client):
    assert client.post("/api/analyze", params={"tariff": -1}).status_code == 422


def test_frontend_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "EcoBiz Copilot" in res.text
    for asset in ("/app.js", "/styles.css"):
        assert client.get(asset).status_code == 200


def test_insight_falls_back_offline_without_api_key(client, monkeypatch):
    """No GEMINI_API_KEY in this test environment: the demo must still return
    a usable recommendation instead of a 503, built from the verified numbers."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    analysis = client.post("/api/analyze").json()
    res = client.post(
        "/api/insight",
        json={
            "source": analysis["source"],
            "summary": analysis["summary"],
            "anomalies": [d for d in analysis["series"] if d["is_anomaly"]],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["model"] == "offline-fallback"
    assert str(round(analysis["summary"]["total_excess_kwh"], 2)) in body["insight"] or "kWh" in body["insight"]
