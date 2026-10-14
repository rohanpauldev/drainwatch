# DrainWatch BD

Ward-level waterlogging prioritisation tool (student prototype for BIP World Town Planning Day 2026, Apps4Solutions track).

It ranks **reported** drainage problem locations for inspection by combining citizen/ward reports with a **live rainfall forecast** from the Open-Meteo API. It does not predict which street will flood.

## Status: build in 6 parts

| Part | What | State |
| --- | --- | --- |
| 1 | Core engine: scoring, Open-Meteo client, data loader, demo data, tests, CLI | done |
| 2 | Streamlit dashboard: ranked queue, score breakdown, rainfall, place search, weight sliders | done |
| 3 | Map, affected assets, citizen report form, status workflow | done |
| 4 | Policy insight panel, CSV export, optional Gemini brief | done |
| 5 | Deploy-ready package and QA | done |
| 6 | Video and submission kit | |

See `DEPLOY.md` for deployment and the pre-submission checklist; `python -m drainwatch.qa` runs the live pre-flight checks.

## Run the dashboard (Part 2)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Search any town in the sidebar: the live Open-Meteo rainfall updates and the queue re-ranks. If the live call fails the app shows the last good response labelled **cached**; with none it ranks without the rainfall factor (scores marked partial). If place search is down, three preset places are offered.

## Run the engine checks (Part 1)

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # 64 tests, no network needed
python -m drainwatch.cli --place Dhaka  # live API call + ranked queue
```

If the live call fails, the CLI says so and ranks without the rainfall factor (scores are marked partial) instead of inventing a number.

## Workflow (Part 3)

- **Map:** OpenStreetMap tiles, marker colour = band, number = rank, grey = resolved. Click a marker or use the selector.
- **Affected assets:** school, clinic, emergency, market and route access come from the record; a public-health flag shows when reported severity is 4 or 5. These are flags, not predictions.
- **Citizen report:** new location or another complaint at a listed site (repeat complaints raise the recurrence score and reopen the site). Photo links must be http(s). After submitting, the resident sees the report ID, rank and status; any ID can be looked up.
- **Status:** Inspected / Resolved / Reopen with a recent-changes log.
- **Persistence:** reports and status live in the browser session only (Streamlit Cloud files are not permanent). Use "Download hotspots (CSV)" to keep them; "Reset demo data" restores the demo set.

## Policy, export and brief (Part 4)

- **Policy insight:** a headline pattern built from counts in the top 5 open sites (for example "3 of the top 5 open sites are school access roads"), plus inspect-first sites, repeat-complaint and data-gap notes. Every sentence is derived from the table on screen; demo data is labelled as an illustration.
- **Export:** "Download ranked queue (CSV)" includes scores, factor breakdown, affected assets and the rainfall used. Reporter text starting with `=`, `+`, `-` or `@` is neutralised against spreadsheet formula injection.
- **Gemini brief (optional):** appears only when `GEMINI_API_KEY` is set in Streamlit secrets (never in the repo; optional `GEMINI_MODEL`). Only site names, scores and bands are sent. Output is labelled as an AI draft.

## Scoring (design assumptions, not validated coefficients)

| Factor | Weight | 0 to 100 from |
| --- | --- | --- |
| Reported severity | 40% | rating 1 to 5, linear |
| Recurrence | 25% | complaints in 12 months, capped at 5 |
| Rainfall exposure | 25% | forecast 24h mm / 80 mm, capped |
| Critical access | 10% | school/clinic/hospital 100, market/main road 50, none 0 |

Bands: 70+ High, 40 to 69 Medium, below 40 Monitor. Missing factors are dropped and the remaining weights re-normalised; the row is flagged as a partial score.

## Data

`data/hotspots.csv` holds **demo records** placed around Mirpur, Dhaka (invented, not real reports). The `source` column is mandatory (`demo`, `field` or `citizen`) so invented rows are never mistaken for real reports.
