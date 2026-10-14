# DrainWatch BD

**Which reported drainage problems should a ward inspect first before the rain, and why?**

DrainWatch BD is a ward-level decision-support prototype. It combines citizen and ward reports with a live rainfall forecast to produce a ranked inspection queue, and it shows the reasoning behind every rank so a planner can check it, challenge it and adjust it.

> It ranks **reported** locations for inspection using the evidence available. It does **not** predict which street will flood.

Built for the BIP World Town Planning Day 2026 **Apps4Solutions** challenge (Track B). Built with AI tools.

**Live app:** _add your `*.streamlit.app` link here_ &nbsp;·&nbsp; **Team:** _add names here_

Supports **SDG 11** (sustainable cities and communities), **SDG 13** (climate action) and **SDG 6** (clean water and sanitation), and the New Urban Agenda's emphasis on working with communities.

---

## The idea

When it rains, a road stops. Complaints pile up, and the ward cannot tell which to inspect first. DrainWatch BD starts from the resident's report and ends at a prioritised, tracked inspection.

| Stakeholder | What they do in the app | What they get back |
| --- | --- | --- |
| Citizens | Report a location, severity (1 to 5), a note and an optional photo link | Their report's rank and status |
| Ward or paurashava staff | Work the ranked queue before rain; mark sites inspected or resolved | A short, explained list instead of scattered complaints |
| NGOs and academia | Add or validate reports; review and adjust the scoring weights | A transparent method they can critique and improve |
| Policy makers | Read the policy insight panel | Patterns that can become rules, such as clearing school-access drains before monsoon |

## What the app does

- **Live rainfall:** hourly precipitation and probability for the next 24 and 48 hours from the [Open-Meteo](https://open-meteo.com/) API (no API key). Search any town and the forecast and the ranking update. If the live call fails, the app shows the last good response labelled **cached**, or ranks without rainfall and marks scores partial. It never invents a number.
- **Ranked inspection queue:** every site gets a 0 to 100 priority score, a band (High, Medium or Monitor) and a bar showing which factors drove it.
- **Explained scores:** pick any site to see each factor's raw input, 0 to 100 score, weight used and points contributed.
- **Map:** OpenStreetMap tiles; marker colour is the band, the number is the queue rank, grey is resolved. Click a marker to open the site.
- **Affected assets:** the school, clinic, emergency, market or main-road access a site may affect, plus a public-health flag for severe reports. These are flags from the record, not predictions.
- **Citizen report form:** a new location, or another complaint at a listed site (repeat complaints raise the recurrence score and reopen the site). The resident sees the report ID, rank and status and can look it up later.
- **Status tracking:** Inspected, Resolved and Reopen, with a log of recent changes.
- **Policy insight:** a headline pattern built from the top 5 open sites, for example "3 of the top 5 open sites are school access roads", plus repeat-complaint and data-gap notes. Every sentence comes from counts in the table on screen.
- **Export:** download the ranked queue (scores, breakdown, assets, rainfall used) or the hotspot data as CSV.
- **Optional AI brief:** a plain-language summary from the Gemini API, shown only if a key is configured. Only site names, scores and bands are sent.

## How scoring works

Four factors, each turned into 0 to 100 and combined with weights:

| Factor | Default weight | Turned into 0 to 100 by | Source |
| --- | --- | --- | --- |
| Reported severity | 40% | Rating 1 to 5, scaled linearly | Citizen or ward report |
| Recurrence | 25% | Complaints in the last 12 months, capped at 5 | Hotspot data |
| Rainfall exposure | 25% | Forecast 24 hour rainfall in mm divided by 80 mm, capped at 100 | Open-Meteo, live |
| Critical access | 10% | 100 for a school, clinic or hospital; 50 for a market or main road; 0 otherwise | Hotspot data |

Bands: **70 and above High**, **40 to 69 Medium**, **below 40 Monitor**.

**Worked example:** a school access road with severity 4 (75), 4 complaints (80), 40 mm forecast (50) and a school affected (100) scores 0.40 × 75 + 0.25 × 80 + 0.25 × 50 + 0.10 × 100 = **72.5, High**. This example is covered by a test.

Rules that keep it honest:

- **Every score shows its breakdown.** There is no black box and no machine learning.
- **Missing data is marked missing, never treated as zero.** A missing factor is dropped, the remaining weights are re-scaled to total 100%, and the row is tagged "partial score".
- **Rainfall is shared across the ward.** One forecast covers the ward, so rainfall lifts or lowers every score together; the other factors decide the order.
- **Weights and thresholds are design assumptions**, not validated engineering values. They are exposed as sliders so planners can test other judgements.

## Data and honesty

`data/hotspots.csv` contains **10 invented demo records** placed around Mirpur, Dhaka. The app shows a "DEMO DATA" banner while every site is demo, and tags each demo row. The `source` column is mandatory (`demo`, `field` or `citizen`) so invented rows can never be mistaken for real reports.

| Column | Meaning |
| --- | --- |
| `id`, `name`, `ward`, `lat`, `lon` | Identity and location |
| `severity_1to5`, `complaints_12m` | Report evidence (may be blank = missing) |
| `critical_access`, `access_note` | `school`, `clinic`, `hospital`, `market`, `main_road`, `none` or blank (not surveyed) |
| `status` | `pending`, `inspected` or `resolved` |
| `source` | `demo`, `field` or `citizen` |
| `note`, `photo_url` | Optional reporter note and http(s) photo link |

To use real reports, replace the rows with your ward's data and set `source` to `field` or `citizen`.

## Limits

- It ranks reported sites. It does not predict flooding, and the rainfall figure is a forecast total for the whole ward.
- Scores are only as good as the reports behind them; places nobody has reported are invisible to it.
- Reports and status changes are held in the browser session (free hosting has no permanent storage). Use the CSV download to keep them.
- Anyone can change a status in this prototype. A real deployment would restrict that to ward staff and add validation by NGOs or academia.

## Run it

Requires Python 3.10 or newer.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Checks:

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # 64 tests, no network needed
python -m drainwatch.qa      # live pre-flight check (needs internet)
python -m drainwatch.cli --place Dhaka   # command-line ranked queue
```

Optional AI brief: copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add a Google AI Studio key. Never commit that file (it is git-ignored). Deployment steps and the pre-submission checklist are in [DEPLOY.md](DEPLOY.md).

## Project layout

```
app.py                   Streamlit dashboard
drainwatch/
  scoring.py             four-factor score, bands, ranking (no UI code)
  weather.py             Open-Meteo geocoding and forecast client
  data.py                CSV loading, validation and saving
  workflow.py            citizen reports, status changes, affected assets
  dashboard.py           queue table, score bars, rainfall fallback logic
  mapview.py             Plotly map
  policy.py              policy insight text
  export.py              CSV export
  gemini.py              optional AI brief
  qa.py, cli.py          pre-flight checks and command-line view
data/hotspots.csv        demo records (Dhaka)
tests/                   unit and app-level tests
```

## Tech stack

Python and Streamlit, Plotly with OpenStreetMap tiles, pandas, Open-Meteo (forecast and geocoding), and the optional Gemini API. Hosted on Streamlit Community Cloud, with Hugging Face Spaces as a fallback. The smallest stack that is live, explainable and demonstrable.

## Acknowledgements

Weather data by [Open-Meteo](https://open-meteo.com/) (CC BY 4.0). Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors. Built with AI assistance as the competition requires.
