from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import job_hunter

ROOT = Path(__file__).resolve().parents[1]
PREFERENCES_PATH = ROOT / "preferences.json"
DEFAULT_PREFERENCES = {
    "location": "Hyderabad/Telangana",
    "include_remote": True,
    "remote_only": False,
    "skills": ["Python", "Django", "SQL"],
    "status": "all",
    "source": "all",
}

app = FastAPI(title="Fresher Job Hunter API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_preferences() -> dict[str, Any]:
    if not PREFERENCES_PATH.exists():
        return DEFAULT_PREFERENCES.copy()
    try:
        with PREFERENCES_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        merged = DEFAULT_PREFERENCES.copy()
        merged.update(data)
        return merged
    except (OSError, ValueError):
        return DEFAULT_PREFERENCES.copy()


def save_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    prefs = DEFAULT_PREFERENCES.copy()
    prefs.update(payload or {})
    with PREFERENCES_PATH.open("w", encoding="utf-8") as fh:
        json.dump(prefs, fh, indent=2, ensure_ascii=False)
    return prefs


def serialize_job(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["required_skills"] = [
        item.strip() for item in str(payload.get("required_skills") or "").split(";") if item.strip()
    ]
    return payload


def get_db_connection() -> sqlite3.Connection:
    con = sqlite3.connect(job_hunter.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


@app.on_event("startup")
def startup_event() -> None:
    job_hunter.init_db()


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        with closing(get_db_connection()) as con:
            con.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "ok"}
    except Exception as exc:  # pragma: no cover - defensive
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc)})


@app.get("/api/preferences")
def get_preferences() -> dict[str, Any]:
    return load_preferences()


@app.post("/api/preferences")
def set_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    return save_preferences(payload)


@app.get("/api/jobs")
def list_jobs(
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    search: str | None = Query(default=None),
    location: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    job_hunter.init_db()
    filters: list[str] = []
    params: list[Any] = []

    if status and status != "all":
        filters.append("status = ?")
        params.append(status)
    if source and source != "all":
        filters.append("source = ?")
        params.append(source)
    if location and location != "all":
        filters.append("location LIKE ?")
        params.append(f"%{location}%")
    if search:
        search_pattern = f"%{search}%"
        filters.append("(title LIKE ? OR company LIKE ? OR location LIKE ? OR match_reason LIKE ? OR email_subject LIKE ?)")
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern, search_pattern])

    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
    count_sql = f"SELECT COUNT(*) FROM jobs{where_clause}"

    with closing(get_db_connection()) as con:
        total = con.execute(count_sql, params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM jobs{where_clause} ORDER BY match_score DESC, first_seen DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return {"items": [serialize_job(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/jobs/{job_key:path}")
def get_job(job_key: str) -> dict[str, Any]:
    with closing(get_db_connection()) as con:
        row = con.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(row)


@app.patch("/api/jobs/{job_key:path}")
def update_job_status(job_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "").strip()
    if not status:
        raise HTTPException(status_code=400, detail="status is required")

    with closing(get_db_connection()) as con:
        cursor = con.execute(
            "UPDATE jobs SET status = ? WHERE job_key = ?",
            (status, job_key),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Job not found")
        con.commit()
    return {"job_key": job_key, "status": status}


@app.post("/api/jobs/run")
def run_job_search() -> dict[str, Any]:
    job_hunter.init_db()
    jobs = job_hunter.deduplicate_jobs(
        job_hunter.fetch_arbeitnow() + job_hunter.fetch_remotive() + job_hunter.fetch_jobicy()
    )
    matches: list[dict[str, Any]] = []
    for job in jobs:
        score, reason = job_hunter.score_job(job)
        if score < 35:
            continue
        subject, draft = job_hunter.gemini_draft(job)
        job_hunter.store_job(job, score, reason, subject, draft)
        job["match_score"] = score
        job["match_reason"] = reason
        job["email_subject"] = subject
        job["email_draft"] = draft
        matches.append(job)
    job_hunter.sort_jobs(matches)
    return {"fetched": len(jobs), "matches": len(matches), "items": matches[:20]}


@app.get("/api/diagnostics")
def diagnostics() -> dict[str, Any]:
    raw_records = [
        ("Arbeitnow", item, False) for item in job_hunter.fetch_arbeitnow_records()
    ]
    raw_records.extend(("Remotive", item, True) for item in job_hunter.fetch_remotive_records())
    raw_records.extend(("Jobicy", item, True) for item in job_hunter.fetch_jobicy_records())
    return job_hunter.build_diagnostic_report(raw_records)


@app.post("/api/jobs/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    save: bool = Form(False),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")

    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        content = await file.read()
        temp_file.write(content)

    try:
        report = job_hunter.preview_csv_import(temp_path)
        if save:
            job_hunter.init_db()
            for job in report["accepted"]:
                subject, draft = job_hunter.make_email_draft(job)
                job_hunter.store_job(job, job["match_score"], job["match_reason"], subject, draft)
        return {"report": report, "saved": save}
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:  # Python < 3.8 guard
            if temp_path.exists():
                temp_path.unlink()


@app.get("/api/jobs/export/csv")
def export_jobs_csv() -> StreamingResponse:
    with closing(get_db_connection()) as con:
        rows = con.execute("SELECT * FROM jobs ORDER BY match_score DESC, first_seen DESC").fetchall()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=job_hunter.CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "title": row["title"],
                "company": row["company"],
                "location": row["location"],
                "url": row["job_url"],
                "required_skills": row["required_skills"],
                "experience_level": row["experience_level"],
                "posted_at": row["posted_at"],
                "match_score": row["match_score"],
                "source": row["source"],
            }
        )

    output = buffer.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(output),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="matched_jobs.csv"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
