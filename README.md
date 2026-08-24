# EcoBiz Copilot

Working MVP for the **EcoFin** track of Future Minds Hackathon 2026. The service
analyzes a school's or small business's daily electricity consumption, finds
where the building was still burning energy during closed periods, and turns
that into a concrete KZT and CO2 savings figure plus a plain-language
recommendation.

## What the MVP does

- accepts a CSV/XLSX with `date` and `consumption_kwh` (optionally `is_workday`);
- if `is_workday` is missing, derives it from weekends plus Kazakhstan public
  holidays and school breaks (`holidays_kz.json`) — a school-supplied schedule
  is always trusted over the calendar guess;
- finds anomalies via the lower quartile of non-working-day consumption;
- reports the excess kWh, potential savings in KZT, and CO2 avoided;
- flags when the baseline itself is too thin to trust (`baseline_reliable`);
- calls Gemini for a human-readable action plan, but always keeps working
  without a key or internet via a local fallback recommendation;
- serves an interactive dashboard (charts, anomaly table, AI Copilot panel)
  directly from the FastAPI backend.

## Project structure

```text
backend/
  main.py            # FastAPI app: /api/analyze, /api/insight, /api/health
  ai.py               # Gemini client (raises AIUnavailable on no key/network/error)
  schemas.py          # Pydantic request/response contract
core.py               # load/clean data, anomaly detection, impact calculation
calendar_utils.py     # weekend + holiday/school-break -> is_workday
holidays_kz.json      # sourced KZ public-holiday and school-break calendar
config.py             # tariff/CO2 sourcing notes the team must double-check
frontend/              # static dashboard (vanilla HTML/CSS/JS, no build step)
data/
  sample_data.csv     # bundled demo dataset (December, with a break-week anomaly)
  generate_sample.py  # regenerates the demo dataset
tests/                # pytest suite (core logic, calendar, API contract)
scripts/run.sh        # venv + uvicorn launcher
```

## Quick start

**Live demo:** https://hazkathon.onrender.com

Needs Python 3.10+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000` for the dashboard, or `http://127.0.0.1:8000/docs`
for the interactive FastAPI docs (useful for a code-free demo of `/api/analyze`
with `data/sample_data.csv`).

```bash
curl http://127.0.0.1:8000/api/health
```

## API

**`POST /api/analyze`** — `multipart/form-data`, `file` optional (falls back to
the bundled sample), `tariff` optional query param (KZT/kWh). Returns a
`summary` (excess kWh, savings, CO2, `baseline_reliable`, `off_day_samples`)
and the full daily `series`.

**`POST /api/insight`** — takes the `summary`/`series` from `/api/analyze` and
returns a Markdown action plan. Uses Gemini when `GEMINI_API_KEY` is set and
reachable; otherwise returns a locally generated recommendation built from the
same verified numbers (`model: "offline-fallback"` in the response) so a demo
never breaks over Wi-Fi.

**`GET /api/health`** — liveness check.

### Minimal input format

```csv
date,consumption_kwh
2026-03-19,420.5
2026-03-20,410.2
```

### With an exact school schedule

```csv
date,consumption_kwh,is_workday
2026-03-19,420.5,0
2026-03-20,410.2,0
```

`is_workday=1` is a normal working day, `0` is non-working. If a row is
missing, invalid, or has a negative `consumption_kwh`, `/api/analyze` returns
a `422` with a specific message instead of silently skewing the result.

## How the detection works

1. Take only non-working days (from the file, or auto-derived via the KZ calendar).
2. Baseline = 25th percentile of their consumption — robust to the very waste
   we're trying to detect, unlike a plain average.
3. A day is an anomaly when `consumption_kwh > baseline x 1.5`.
4. Excess on an anomaly day: `actual - baseline`.
5. Savings: `excess x tariff`; CO2 avoided: `excess x co2_factor`.

This is statistical anomaly detection over a time series, not a neural model —
a deliberate MVP choice: it's honest, explainable in a Q&A, and sufficient for
this dataset size. `baseline_reliable` is `False` when fewer than 5 non-working
days are available; the result is still shown, but should not be presented as
a confirmed fact.

Gemini is used for a different, explainable job: turning the verified numbers
into a short action plan for a non-technical facility manager. Every number
and date in the recommendation comes from the analysis, never from the model.

## Tariffs and CO2 — an honest limitation

The default tariff (17.447 KZT/kWh) is a real household tariff for the Abai
region, cited in the project brief — **not** a confirmed rate for any specific
school or business. `config.py` documents this plus alternate regional tariffs
with sources; confirm the actual contracted tariff before presenting final
numbers, and pass it via the `tariff` parameter. The default CO2 factor
(0.85 kg/kWh) is likewise a demonstration assumption pending a confirmed
methodology — see `config.CO2_FACTOR_TODO`.

## Deploy

The repo includes `render.yaml` so [Render](https://render.com) can deploy the whole app (backend +
static frontend, no database) as a single free-tier Python web service:

1. Push this repo to GitHub (already done: `azitem1234-ctrl/hazkathon`).
2. On Render: **New → Blueprint**, connect the repo — it reads `render.yaml` automatically
   (build: `pip install -r requirements.txt`, start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`).
3. In the service's **Environment** tab, set `GEMINI_API_KEY` to your key (it is declared in
   `render.yaml` as a secret with no value, so Render will prompt for it — never commit the key itself).
4. Deploy. Once live, confirm `/api/health`, `/api/analyze`, `/api/insight` and the dashboard at `/`
   all work on the public `*.onrender.com` URL exactly like they do locally — `/api/insight` should
   still return the offline-fallback recommendation if the key or network is ever unavailable on the
   host, so a live demo never hard-fails.
5. Paste the resulting URL into the **Live demo** line under [Quick start](#quick-start).

Railway works the same way (Python service, same build/start commands, same env var) if preferred over
Render — there's no Render-specific code, just the blueprint file.

## Gemini setup

1. Copy `.env.example` to `.env`.
2. Fill in `GEMINI_API_KEY`.
3. Never commit `.env`.

Without a key, with no network, or on a Gemini error, `/api/insight`
automatically returns the offline fallback recommendation — the demo does not
depend on Wi-Fi.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Covers the anomaly detector, the impact math, the calendar auto-fill, the
offline AI fallback, and the API contract (status codes, required columns,
tariff scaling).

## Q&A prep: what each teammate should be able to explain

**Data and the statistical core**

- *Why the 25th percentile, not the mean?* The mean can be dragged up by the
  very anomalies we're trying to catch; the 25th percentile stays closer to a
  correctly shut-down building.
- *Why a 1.5x threshold?* A simple, explainable MVP threshold — consumption
  has to be clearly above baseline, not just slightly. It can become a setting
  once there's more real history to tune against.
- *Why can `baseline_reliable` be false?* With 1-4 non-working days you can't
  honestly claim a norm. The code still shows a preliminary signal but does
  not hide the weakness of the data.
- *What happens with `is_workday` missing?* It's derived from weekends plus
  `holidays_kz.json` (Kazakhstan public holidays and typical school breaks,
  sourced from gov.kz). A manually supplied column always wins.

**Gemini and the API**

- *Why use an LLM if the math works without it?* The formula finds the
  problem; the LLM translates it into an action a non-technical person can
  take. It's a distinct function, not "chat for chat's sake."
- *How do you stop Gemini from inventing numbers?* The prompt supplies only
  verified figures from `impact`/`summary`, and the same numbers are echoed
  back into the response by the app itself — the model's job is the narrative,
  not the arithmetic.
- *What happens with no internet?* `/api/insight` never 503s for that reason —
  it returns a templated recommendation built from the same verified numbers.
- *Why is CORS wide open?* Local development convenience only; before any
  public deployment the allowed origin would be restricted to the app's domain.

Before the defense, each teammate should run the project locally, read "How
the detection works" above, and be ready to answer these in their own words —
per the hackathon's AI-policy rules, inability to explain a piece of the code
zeroes out the technical-defense score for that fragment.
