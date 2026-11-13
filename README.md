# Fresher Job Hunter Agent (MVP)

A small, low-cost Python job discovery and matching tool. It fetches public job listings,
scores them against a fresher Python/Django profile, stores matches in SQLite, and drafts
a personalized outreach email. It does **not** send emails or submit applications.

## Requirements
- Windows + Python 3.11 or newer
- Internet connection
- Optional Gemini API key from https://aistudio.google.com/apikey

## Setup (PowerShell)
```powershell
Set-Location D:\fresher_job_hunter
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, run this in the same terminal, then activate again:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

## Optional Gemini setup
Open `.env` and paste your Gemini API key after `GEMINI_API_KEY=`.
Keep `.env` private; never commit it to GitHub. A Gemini app subscription and Gemini API
billing are separate. The script works without a key by using a rule-based email template.
Check current model access and free-tier limits in Google AI Studio.
The Gemini client package is optional. To enable AI-written drafts, install it with
`pip install -r requirements-ai.txt`; the base requirements are enough for normal use.

## Run
```powershell
Set-Location D:\fresher_job_hunter
.\.venv\Scripts\python.exe .\job_hunter.py
```
To review a local CSV file without saving it:
```powershell
Set-Location D:\fresher_job_hunter
.\.venv\Scripts\python.exe .\job_hunter.py --import-csv .\jobs_to_review.csv
```
Only accepted matches are shown in a preview, with Hyderabad/Telangana jobs
listed first. Live results rank Hyderabad first, then other Telangana locations, then
India-eligible remote jobs; within a location group, higher scores and newer known
posting dates rank first. Rejected rows include their CSV row number and reason. Preview mode
does not write to SQLite or overwrite the normal JSON/CSV exports.

After reviewing the preview, explicitly save accepted listings to SQLite with:
```powershell
Set-Location D:\fresher_job_hunter
.\.venv\Scripts\python.exe .\job_hunter.py --import-csv .\jobs_to_review.csv --save-import
```
This uses the local template to create an email draft in the database; it never
sends email, submits applications, or contacts recruiters. No API is called.

CSV requires a job-title column and an original application URL column. Other
supported columns include company, location, description, experience, job type,
posting date, source, remote, and tags. Common aliases such as `title`,
`job_title`, `original_url`, `application_url`, and `apply_url` are accepted.
Blank experience does not count as fresher eligibility; a relevant job-type or
description must explicitly identify a fresher, entry-level, graduate, 0–1-year, or
internship role. A "junior" title by itself is not sufficient. Conflicting fresher or
internship language and a 2+ year requirement is rejected for manual review. A valid
original HTTP(S) job URL is required for a match. Remote locations must name India or
clearly state global/worldwide availability; vague "anywhere" text and explicit India
exclusions do not qualify.

Example `jobs_to_review.csv`:
```csv
title,company,location,description,experience,job_type,posted_at,source,url,remote,tags
"Junior Python Developer","Example Co","Hyderabad, Telangana","Fresher role using Python and Django","0-1 year","Full-time","2026-10-01","Permitted source","https://jobs.example/apply/123","no","Python; Django REST Framework; SQL"
"Software Engineering Intern","Example Remote Co","India","Internship building Python backend services","","Internship","2026-10-02","Permitted source","https://jobs.example/apply/456","yes","Python; SQL"
```
Import only listings from sources whose terms permit your intended use. The
importer does not verify source permissions or fetch/check live listings.

To inspect the current source batch without creating drafts, writing job records,
or exporting files:
```powershell
Set-Location D:\fresher_job_hunter
.\.venv\Scripts\python.exe .\job_hunter.py --diagnose
```
Diagnostics report missing fields, independent eligibility counts, sequential
pipeline counts, and a few anonymized rejection examples. It does not write job
records or exports; normal application logging can update `job_hunter.log`.

The first run creates:
- `job_hunter.db`: SQLite database with discovered matches
- `matched_jobs.json`: matching listings and email drafts
- `matched_jobs.csv`: spreadsheet-friendly normalized job details and scores
- `job_hunter.log`: rotating application log

The sources currently include:
- Arbeitnow public job-board API
- Remotive public remote-jobs API (software-dev category; listings may be delayed)
- Jobicy public remote-jobs API

API coverage is not exhaustive and local Hyderabad fresher roles may be missed. The
current feeds have not provided reliable Hyderabad fresher coverage. Verify each listing
on its original site before applying. Remotive requires linking back to the original
listing and naming Remotive as the source if you display its data elsewhere. Public
endpoint availability alone does not establish broad redistribution rights; no additional
feed is integrated until access and reuse terms are clearer.
Arbeitnow is requested once per run, and Remotive is queried for its software-dev
category with a 200-listing API limit. Neither adapter currently searches employer
career pages directly.
Required skills are inferred from keywords in job descriptions, and experience filtering is
heuristic; review the original listing before applying. Exact URLs (apart from common tracking
parameters) are used to deduplicate listings, so reposts with different URLs may remain.
Repeated discoveries update SQLite's `last_seen` value while preserving `first_seen`.
The application does not send new-job notifications.

Jobicy's public API is queried once per page at up to 200 listings, following its cursor
while additional pages are available. The public feed is free and requires no API key;
it contains postings published in the preceding seven days. Jobicy's API usage guidance
permits use in personal products/tools, asks that Jobicy be credited and its canonical
listing URL be preserved, and says to avoid excessive requests. No numeric request quota
is published; HTTP 429 is logged and left for a later scheduled run rather than retried
immediately. The free response's URL points to the Jobicy listing, not directly to the
employer's application. Direct ATS links are an optional commercial feature priced at
$0.01 per available direct-link job, and this application does not use that feature.

The Jobicy location taxonomy includes APAC but not India as a separate geo filter.
APAC-only listings are not treated as India-eligible by this application. A Jobicy posting
must explicitly identify India or global eligibility to pass the unchanged location
filter; therefore this feed may provide no matches for India on some runs. Its listing
metadata and descriptions remain subject to manual verification.

The draft uses only the skills and Optimal Guide DSA project from the configured profile.
Degree and GitHub details are omitted unless `CANDIDATE_DEGREE` and
`CANDIDATE_GITHUB_URL` are set in `.env`. The application never sends emails or submits jobs.

Run the offline tests from the project directory:
```powershell
Set-Location D:\fresher_job_hunter
.\.venv\Scripts\python.exe -m unittest -v
```

## Full-stack app (FastAPI + React + Vite)

The project now includes a lightweight full-stack dashboard built on the existing Python job-hunting logic.

### Start the backend
```powershell
Set-Location D:\fresher_job_hunter
. .\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Or use the helper script:
```powershell
Set-Location D:\fresher_job_hunter
. .\start_backend.ps1
```

### Start the frontend
```powershell
Set-Location D:\fresher_job_hunter\frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Or use the helper script:
```powershell
Set-Location D:\fresher_job_hunter
. .\start_frontend.ps1
```

### Start both together
```powershell
Set-Location D:\fresher_job_hunter
. .\start_full_stack.ps1
```

The frontend serves at `http://localhost:5173` and proxies `/api/*` requests to the FastAPI backend on `http://localhost:8000`.

The backend exposes endpoints for health, jobs, diagnostics, CSV import, CSV export, preferences, and application tracking while reusing the existing SQLite database and matching logic.

## Daily automation (Windows Task Scheduler)
1. Open Task Scheduler → Create Basic Task.
2. Name it `Fresher Job Hunter Daily`.
3. Trigger: Daily, choose a time.
4. Action: Start a program.
5. Program/script: the full path to `.venv\Scripts\python.exe`.
6. Add arguments: the full path to `job_hunter.py`.
7. Start in: the project folder.
8. Finish, then right-click the task → Run to test.

## Important safety and accuracy notes
- This MVP does not scrape LinkedIn or private pages.
- It stores recruiter contact fields only if the public listing provides them; it does not
  guess email addresses or harvest personal contact data.
- It never sends email or submits an application automatically.
- Check eligibility, date, location, and employer legitimacy before applying.
- The match score is a keyword heuristic, not a guarantee of suitability.
- Job sources and API terms can change; respect each source's terms and request limits.
