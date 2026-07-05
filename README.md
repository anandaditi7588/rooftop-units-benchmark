# RTU Heat Pump Benchmarking

Automated benchmarking of commercial HVAC **Heat Pump Rooftop Units** across
multiple manufacturers — Carrier, Trane, Lennox, Johnson Controls (York),
Daikin, Rheem, AAON — with AI-assisted parameter matching, a formatted Excel
comparison report, and an interactive dashboard.

---

## 1. What this actually does (read this first)

This is a real, working pipeline, not a mock-up:

- **PDF extraction** (`core/pdf_extractor.py`) uses PyMuPDF + pdfplumber to
  pull text and tables out of real manufacturer PDFs, with an optional OCR
  fallback for scanned pages.
- **Matching** (`core/matching.py`) combines a synonym dictionary, RapidFuzz
  string similarity, and (optionally) sentence-transformer embeddings /
  TF-IDF cosine similarity to map arbitrary manual wording ("Net Cooling",
  "ESP", "Sound Power") onto your Column B parameter names.
- **Excel generation** (`core/excel_io.py`) writes a fully formatted
  workbook with frozen headers, autofilter, alternating rows, brand-colored
  competitor columns, and conditional highlighting for missing values,
  cross-competitor discrepancies, and best-in-class numeric values.
- **Dashboard** (`templates/`, `static/`) is a real Bootstrap 5 + Chart.js +
  DataTables UI reading `output/benchmark.json`.

**One honest limitation:** manufacturer websites gate a lot of their spec
sheets behind region selectors, JS-rendered catalogs, or lead-capture forms,
and their markup changes over time. No scraper can promise to always find
the "right" PDF automatically and keep working forever. So the scraper
(`core/scraper.py`) is a real, working, domain-allow-listed crawler — **and**
the pipeline always checks `source_documents/<Competitor>/` for manually
dropped PDFs *first*, before attempting any live scraping. That manual path
always works, even with no internet access, and is what most engineering
teams end up relying on for the manufacturers whose sites resist automated
discovery. Toggle scraping on/off per run from the setup page.

---

## 2. Project layout

```
app.py                        FastAPI app & routes (entrypoint)
core/
  config.py                   Paths, settings, competitor/synonym registries
  schemas.py                  Pydantic models shared by API/pipeline
  logging_setup.py            App-wide + per-job logging
  excel_io.py                 Read Physical_Data.xlsx / write comparison.xlsx
  matching.py                 Synonym + fuzzy + semantic matching engine
  pdf_extractor.py            PDF text/table/OCR extraction
  scraper.py                  Official-domain web scraping engine
  job_manager.py              Background job tracking (progress polling)
  pipeline.py                 End-to-end orchestration
scripts/
  generate_sample_physical_data.py   Creates Physical_Data.xlsx (project root) if missing
templates/                    index.html (setup) & dashboard.html
static/                       css / js / icons
config/
  competitors.json            Add a new competitor here — no code changes
  parameter_synonyms.json     Domain-knowledge synonym groups
  parameter_rules.json        higher/lower-is-better directionality
Physical_Data.xlsx (project root)   Master benchmark parameter template (Column B)
data/                         Reserved for any additional data assets you add
uploads/                      User-uploaded parameter sheets
output/                       comparison.xlsx + benchmark.json (generated)
source_documents/<Competitor>/  Drop manufacturer PDFs here manually
logs/                         app.log + logs/jobs/<job_id>.log
```

---

## 3. Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
```

Optional extras (sentence-transformers, pytesseract, playwright, camelot) are
listed at the bottom of `requirements.txt` — install them only if you want
that specific capability; everything degrades gracefully without them.

Run the server:

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 to configure a run, or http://localhost:8000/dashboard
to view the latest results.

---

## 4. Using it

1. **Setup page (`/`)** — select competitors, choose whether to use the
   bundled `Physical_Data.xlsx` or upload your own (Column B = parameter
   names, Column A optional category, Column C optional expected unit), and
   optionally toggle live web scraping.
2. **Scope it to a specific unit (optional)** — enter a **Series / Model
   Name** if you know it (e.g. `Premier YZ036`), or, if you don't, describe
   the **Unit Configuration** instead (e.g. `25 ton heat pump rooftop unit
   with gas heat and economizer`). Leave both blank to benchmark whatever
   the collected documents contain, with no scoping. See below for how this
   actually narrows the results.
3. Click **Start Automated Benchmarking**. The pipeline runs in the
   background; the page polls `/api/job-status/{job_id}` and shows a live
   progress bar and stage messages.
4. Once complete, use the **Download Excel** / **Download CSV** buttons
   right there on the setup page, or click **View Dashboard** to see KPI
   cards, comparison charts (bar/radar/pie/stacked), a confidence heatmap,
   and the full sortable, filterable, exportable comparison table.
5. Export via the dashboard buttons: **Excel** (fully formatted workbook,
   including a `Sources_Detail` sheet), **CSV**, or **PDF**.

### Scoping a benchmark to one series/model or configuration

Manufacturer manuals and catalogs usually document an entire product
family across dozens of pages/tables. Typing a series name or a
configuration description into Step 2 changes extraction in two ways:

- **Scraper bias** (`core/scraper.py`): link/anchor text mentioning your
  query terms is scored higher than generic "spec sheet" links, so live
  scraping prefers documents about that series.
- **Page-level scoping** (`core/pdf_extractor.py`): before extracting
  label/value candidates, every page of every document is fuzzy-matched
  against your query (RapidFuzz `token_set_ratio` + `partial_ratio`).
  Extraction is then restricted to the pages that plausibly describe that
  unit. If no page matches well enough, the query is treated as "not found
  in this document" and the *whole* document is searched instead — so a
  query that doesn't literally appear anywhere never silently returns
  nothing.

The scoped query is echoed back everywhere the results are: a banner row
at the top of both sheets in `comparison.xlsx`, a banner on the dashboard,
and `unit_query` in `benchmark.json` / the job status response.

### Getting real manufacturer data in

The most reliable way, given how manufacturer sites are structured, is:

```
source_documents/
  Carrier/           <- drop Carrier spec sheets / submittals / IOMs (PDF) here
  Trane/
  Lennox/
  JohnsonControls/
  Daikin/
  Rheem/
  AAON/
```

The pipeline always uses these first. If "Enable live web scraping" is
checked, it will *additionally* try to crawl each competitor's configured
`search_urls` in `config/competitors.json` (restricted to that competitor's
`allowed_domains`) for linked PDFs, and drop anything it finds into the same
folder for reuse on subsequent runs.

---

## 5. Adding a new competitor

Edit `config/competitors.json` and append an object:

```json
{
  "id": "new_vendor",
  "name": "New Vendor",
  "color": "#123456",
  "logo": "new_vendor.svg",
  "homepage": "https://www.newvendor.com",
  "allowed_domains": ["newvendor.com"],
  "search_urls": ["https://www.newvendor.com/products/rooftop-units"],
  "keywords": ["rooftop", "heat pump", "specification"]
}
```

Create `source_documents/NewVendor/` (see the folder-name mapping in
`Competitor.source_dir` in `core/config.py`) and it's immediately selectable
on the setup page — no other code changes required.

## 6. Adding new parameters / synonyms

- Edit `Physical_Data.xlsx` Column B directly, or upload a different
  sheet from the UI.
- To teach the matcher a new synonym relationship, add a group to
  `config/parameter_synonyms.json`.
- To make a numeric parameter eligible for "best value" highlighting, add
  its canonical name to `config/parameter_rules.json`.

## 7. Logs

- `logs/app.log` — application-wide rotating log.
- `logs/jobs/<job_id>.log` — full audit trail for a single benchmarking run:
  which URLs were searched, what was downloaded, every matched/missing
  parameter with its confidence score, and timing.

## 8. Notes on scale & performance

- Designed to comfortably handle 1000+ parameters and 20+ competitors: the
  matching engine's per-candidate cost is linear, table/text extraction is
  streamed per-page, and the job runs off the request thread so the UI stays
  responsive. For very large corpora, swap `JobManager`'s in-process thread
  model for a real task queue (Celery/RQ) — the rest of the pipeline is
  already decoupled from how the job is scheduled.
- Benchmark jobs run one at a time on a single worker (`core/job_manager.py`)
  — PDF extraction is CPU-bound, and several running concurrently fight over
  Python's GIL badly enough to make the whole server feel unresponsive.
  Starting a new job while one is active queues it instead. A job can be
  stopped with `POST /api/cancel-benchmark/{job_id}` (also a **Cancel**
  button on the setup page) — queued jobs cancel instantly, running ones
  cancel after their current document finishes.

## 9. Deploying so someone else can reach it

Locally the app binds to `127.0.0.1`/`localhost` — only this machine can
reach it. To share it with someone remote (not on your local network), it
needs to run somewhere with a public address. This repo is set up to deploy
to **Render** (free tier, deploys straight from GitHub via the included
`Dockerfile`), but the same Dockerfile works on Railway, Fly.io, or any
container host.

### Before deploying: turn on authentication

This app has no per-user login system — anyone who reaches the URL can
start benchmarks, upload files, and see results. Locally that's fine
(only you can reach `localhost`), but once it's public, set these two
environment variables on your hosting platform:

| Variable | Purpose |
|---|---|
| `RTU_AUTH_USERNAME` | Shared username for HTTP Basic Auth |
| `RTU_AUTH_PASSWORD` | Shared password — pick something real, not a placeholder |

Auth is **off** whenever either variable is unset (that's what keeps local
dev frictionless) and turns **on** automatically the moment both are set —
see `core/auth.py`. Anyone reaching the URL will get a browser login prompt.

### Deploying to Render

1. Push this repository to GitHub (create a new repo at github.com, then
   from this project folder: `git remote add origin <your-repo-url>` and
   `git push -u origin master`).
2. Sign up at [render.com](https://render.com) (free, can use your GitHub
   login).
3. **New +** → **Web Service** → connect the GitHub repo you just pushed.
4. Render will detect the `Dockerfile` automatically. Set:
   - **Instance type**: Free (fine for a demo; upgrade if the client will
     run large/frequent benchmarks — PDF extraction is CPU-bound).
   - **Environment variables**: add `RTU_AUTH_USERNAME` and
     `RTU_AUTH_PASSWORD` (see above).
5. Click **Create Web Service**. First build takes a few minutes (installs
   `requirements.txt`, bakes in `Physical_Data.xlsx` and the curated
   `source_documents/*.pdf` files already in the repo). Render gives you a
   URL like `https://your-app-name.onrender.com` — that's what you share
   with your client (with the username/password separately, e.g. by phone
   or a different channel than the link itself).

### Important limitations of this deployment

- **Ephemeral storage on the free tier**: `uploads/`, `output/`, and `logs/`
  reset on every restart/redeploy. `Physical_Data.xlsx` and the PDFs already
  committed to the repo persist fine (they're baked into the image), but
  anything uploaded or generated *while the app is running* — a custom
  parameter sheet, a fresh `comparison.xlsx`, newly-scraped PDFs — is lost
  if the container restarts. Fine for a live demo session; not fine as
  permanent storage. If ongoing use is needed, add Render's persistent Disk
  add-on (paid) mounted at `/app/output`, `/app/uploads`, and
  `/app/source_documents`.
- **Free tier sleeps when idle**: Render's free web services spin down after
  ~15 minutes of no traffic and take ~30-60 seconds to wake back up on the
  next request. The first request after idle time will feel slow —  that's
  expected, not broken.
- **Live web scraping still applies**: manufacturer sites that render their
  document lists via JavaScript (Lennox, Rheem — see section 4) behave the
  same in the cloud as they do locally; the curated PDFs already in the repo
  are what make Carrier/Johnson Controls/Daikin/AAON reliable out of the box.
