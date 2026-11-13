import csv
import argparse
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "job_hunter.db"
OUTPUT_PATH = ROOT / "matched_jobs.json"
CSV_PATH = ROOT / "matched_jobs.csv"
JOBICY_API_URL = "https://jobicy.com/api/v2/remote-jobs"

ROLE_TERMS = [
    "python developer", "django developer", "backend developer",
    "software engineer", "graduate engineer", "python intern",
    "software developer", "full stack developer", "web developer",
    "developer intern", "software intern",
]
SKILL_TERMS = [
    "python", "sql", "django rest framework", "django", "rest api",
    "postgresql", "sqlite", "git", "github", "angular",
    "data structures and algorithms", "dsa",
]
FRESHER_TERMS = [
    "fresher", "entry level", "entry-level", "graduate",
    "intern", "internship", "0-1 year", "0 to 1 year", "0–1 year",
    "0-1 years", "0 to 1 years", "0–1 years", "no more than 1 year",
    "no experience", "new grad", "early career",
]
INTERNSHIP_TERMS = ("intern", "internship", "co-op", "cooperative education")
SENIORITY_TERMS = ("senior", "lead", "principal", "staff", "manager")
EXPERIENCE_PATTERN = re.compile(
    r"\b(?P<minimum>\d+)\s*(?:\+|plus)?\s*"
    r"(?:[-–—]\s*(?P<maximum>\d+)\s*)?(?:years?|yrs?)(?:['’]s?)?\s*"
    r"(?:of\s+)?(?:[A-Za-z][\w+#.-]*\s+){0,3}(?:experience|exp)\b",
    re.IGNORECASE,
)

HEADERS = {"User-Agent": "FresherJobHunter/0.1 (personal job search tool)"}
CSV_FIELDS = (
    "title", "company", "location", "url", "required_skills",
    "experience_level", "posted_at", "match_score", "source",
)
LOGGER = logging.getLogger("job_hunter")


def configure_logging():
    handler = RotatingFileHandler(
        ROOT / "job_hunter.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[handler],
        force=True,
    )


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_key TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                location TEXT,
                job_url TEXT,
                source TEXT,
                posted_at TEXT,
                required_skills TEXT NOT NULL DEFAULT '',
                experience_level TEXT NOT NULL DEFAULT 'Not specified',
                match_score INTEGER,
                match_reason TEXT,
                contact_name TEXT,
                public_contact_email TEXT,
                public_contact_url TEXT,
                email_subject TEXT,
                email_draft TEXT,
                status TEXT DEFAULT 'new',
                first_seen TEXT
            )
        """)
        columns = {
            row[1] for row in con.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "required_skills" not in columns:
            con.execute(
                "ALTER TABLE jobs ADD COLUMN required_skills TEXT NOT NULL DEFAULT ''"
            )
        if "experience_level" not in columns:
            con.execute(
                "ALTER TABLE jobs ADD COLUMN experience_level "
                "TEXT NOT NULL DEFAULT 'Not specified'"
            )
        if "last_seen" not in columns:
            con.execute("ALTER TABLE jobs ADD COLUMN last_seen TEXT")


def get_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(x) for x in value)
    return str(value)


def _has_term(text, term):
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


def _field_terms(value):
    if value is None:
        return []
    if isinstance(value, dict):
        values = value.values()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return [str(value).strip()] if str(value).strip() else []
    terms = []
    for item in values:
        terms.extend(_field_terms(item))
    return terms


def normalize_job(item, source, remote=False):
    """Return the same safe, predictable fields for listings from every source."""
    if not isinstance(item, dict):
        return None
    title = get_text(item.get("title") or item.get("jobTitle")).strip()
    company = get_text(
        item.get("company_name") or item.get("company") or item.get("companyName")
    ).strip()
    location = get_text(
        item.get("location")
        or item.get("candidate_required_location")
        or item.get("jobGeo")
    ).strip()
    description = get_text(
        item.get("description")
        or item.get("jobDescription")
        or item.get("jobExcerpt")
    ).strip()
    experience = get_text(
        item.get("experience")
        or item.get("experience_level")
        or item.get("experience_required")
        or item.get("jobLevel")
    ).strip()
    url = get_text(item.get("url") or item.get("job_url")).strip()
    posted_at = get_text(
        item.get("created_at")
        or item.get("publication_date")
        or item.get("posted_at")
        or item.get("pubDate")
    ).strip()
    tags = _field_terms(item.get("tags") or item.get("jobIndustry"))
    job_types = _field_terms(item.get("job_types") or item.get("jobType"))
    job_types.extend(_field_terms(item.get("job_type")))
    combined = f"{title} {description} {experience} {' '.join(tags)}"
    required_skills = [
        term for term in SKILL_TERMS if _has_term(combined, term)
    ]
    internship = _is_internship(title, description, job_types)

    remote_value = item.get("remote", remote)
    is_remote = remote_value if isinstance(remote_value, bool) else (
        get_text(remote_value).strip().lower() in {"1", "true", "yes"}
    )
    listing_id = get_text(
        item.get("slug") or item.get("id") or item.get("jobSlug") or url or title
    ).strip()
    return {
        "id": listing_id,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "experience": experience,
        "url": url,
        "posted_at": posted_at,
        "required_skills": required_skills,
        "experience_level": get_experience_level(
            title, description, job_types, internship, experience
        ),
        "tags": tags,
        "job_types": job_types,
        "internship": internship,
        "remote": is_remote,
        "source": source,
        # These APIs do not provide verified recruiter-contact fields.
        "contact_name": "",
        "public_contact_email": "",
        "public_contact_url": "",
    }


def fetch_arbeitnow_records():
    """Fetch raw public records from Arbeitnow's public job-board API."""
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data", []) if isinstance(payload, dict) else []
        return items if isinstance(items, list) else []
    except Exception as exc:
        LOGGER.warning("Arbeitnow fetch failed: %s", exc)
        return []


def fetch_arbeitnow():
    """Fetch and normalize public listings from Arbeitnow."""
    return [
        job for item in fetch_arbeitnow_records()
        if (job := normalize_job(item, "Arbeitnow")) is not None
    ]


def fetch_remotive_records():
    """Fetch raw public remote records from Remotive."""
    url = "https://remotive.com/api/remote-jobs"
    try:
        response = requests.get(url, params={"category": "software-dev", "limit": 200},
                                headers=HEADERS, timeout=25)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("jobs", []) if isinstance(payload, dict) else []
        return items if isinstance(items, list) else []
    except Exception as exc:
        LOGGER.warning("Remotive fetch failed: %s", exc)
        return []


def fetch_jobicy_records():
    """Fetch public Jobicy remote postings, following its published cursor."""
    params = {"count": 200}
    records = []
    cursors = set()
    while True:
        try:
            response = requests.get(
                JOBICY_API_URL,
                params=params,
                headers=HEADERS,
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            LOGGER.warning("Jobicy fetch failed: %s", exc)
            return []

        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            LOGGER.warning("Jobicy returned an invalid jobs response.")
            return []
        records.extend(payload["jobs"])
        cursor = payload.get("nextCursor")
        if not cursor:
            return records
        if not isinstance(cursor, str) or cursor in cursors:
            LOGGER.warning("Jobicy returned an invalid or repeated pagination cursor.")
            return records
        cursors.add(cursor)
        params = {"count": 200, "cursor": cursor}


def fetch_jobicy():
    """Normalize Jobicy's public remote listings for the shared matcher."""
    return [
        job for item in fetch_jobicy_records()
        if (job := normalize_job(item, "Jobicy", remote=True)) is not None
    ]


def fetch_remotive():
    """Fetch and normalize public remote listings; may be delayed by 24 hours."""
    return [
        job for item in fetch_remotive_records()
        if (job := normalize_job(item, "Remotive", remote=True)) is not None
    ]


def _experience_requirement(text):
    bounds = _experience_bounds(text)
    if not bounds:
        return ""
    minimum, maximum = bounds
    if minimum != maximum:
        return f"{minimum}-{maximum} years"
    return f"{minimum}+ years" if minimum >= 3 else f"{minimum} years"


def _experience_bounds(text):
    matches = list(EXPERIENCE_PATTERN.finditer(text))
    if not matches:
        return None
    bounds = [
        (
            int(match.group("minimum")),
            int(match.group("maximum") or match.group("minimum")),
        )
        for match in matches
    ]
    return max(bounds, key=lambda item: item[1])


def _valid_job_url(url):
    try:
        parts = urlsplit(get_text(url).strip())
        return (
            parts.scheme.lower() in {"http", "https"}
            and bool(parts.hostname)
        )
    except ValueError:
        return False


def _experience_context(value):
    text = get_text(value).strip()
    if text and _experience_bounds(text) is None and re.search(
        r"\b(?:years?|yrs?)\b", text, re.IGNORECASE
    ):
        return f"{text} experience"
    return text


def _is_internship(title, description, job_types=()):
    text = " ".join((title, description, " ".join(job_types))).lower()
    return any(_has_term(text, term) for term in INTERNSHIP_TERMS)


def get_experience_level(
    title, description, job_types=(), internship=False, experience=""
):
    text = f"{title} {description} {_experience_context(experience)}".lower()
    requirement = _experience_requirement(text)
    if requirement:
        return requirement
    if internship or _is_internship(title, description, job_types):
        return "Internship"
    if any(_has_term(text, term) for term in FRESHER_TERMS):
        return "Fresher / entry-level"
    return "Not specified"


def _target_location(job):
    location = get_text(job.get("location")).lower()
    if "hyderabad" in location or "telangana" in location:
        return "Hyderabad/Telangana"
    india_excluded = (
        re.search(
            r"\b(?:excluding|except|outside|not open to|not available in|"
            r"not hiring in)\b.{0,50}\bindia\b",
            location,
        )
        or re.search(
            r"\bindia\b.{0,30}\b(?:excluded|not eligible|not available|not supported)\b",
            location,
        )
    )
    globally_open = bool(
        re.search(r"\b(?:worldwide|global|globally)\b", location)
        or re.search(r"\banywhere\s+(?:in\s+)?(?:the\s+)?world\b", location)
    )
    india_named = re.search(r"\bindia\b", location)
    if job.get("remote") and not india_excluded and (india_named or globally_open):
        return "Remote (India-eligible)"
    return ""


def match_facts(job):
    title = get_text(job.get("title")).lower()
    location = get_text(job.get("location")).lower()
    body = get_text(job.get("description")).lower()
    tags = _field_terms(job.get("tags"))
    job_types = _field_terms(job.get("job_types"))
    job_types.extend(_field_terms(job.get("job_type")))
    experience = _experience_context(job.get("experience")).lower()
    combined = (
        f"{title} {location} {body} {experience} "
        f"{' '.join(tags)} {' '.join(job_types)}"
    )
    role_hits = [term for term in ROLE_TERMS if _has_term(title, term)]
    skill_hits = [term for term in SKILL_TERMS if _has_term(combined, term)]
    generic_software_title = any(
        _has_term(title, term) for term in ("developer", "engineer", "intern")
    )
    relevant = bool(role_hits) or (generic_software_title and bool(skill_hits))
    experience_bounds = _experience_bounds(combined)
    senior_title = any(_has_term(title, term) for term in SENIORITY_TERMS)
    over_experience_target = (
        experience_bounds is not None and experience_bounds[1] >= 2
    )
    fresher_hits = [term for term in FRESHER_TERMS if _has_term(combined, term)]
    target = _target_location(job)
    internship = (
        bool(job.get("internship"))
        or _is_internship(
            get_text(job.get("title")),
            get_text(job.get("description")),
            job_types,
        )
    )
    explicit_one_year_or_less = (
        experience_bounds is not None and experience_bounds[1] <= 1
    )
    eligibility_signal = bool(fresher_hits) or internship or explicit_one_year_or_less
    experience_eligible = (
        not senior_title and not over_experience_target and eligibility_signal
    )
    location_type = (
        "hyderabad" if target == "Hyderabad/Telangana"
        else "india_remote" if target == "Remote (India-eligible)"
        else ""
    )
    return {
        "role_hits": role_hits,
        "skill_hits": skill_hits,
        "relevant": relevant,
        "senior_title": senior_title,
        "experience_bounds": experience_bounds,
        "over_experience_target": over_experience_target,
        "fresher_hits": fresher_hits,
        "experience_eligible": experience_eligible,
        "eligibility_signal": eligibility_signal,
        "internship": internship,
        "location": target,
        "location_type": location_type,
    }


def score_job(job):
    facts = match_facts(job)
    if not facts["relevant"]:
        if not facts["role_hits"] and not any(
            _has_term(get_text(job.get("title")).lower(), term)
            for term in ("developer", "engineer", "intern")
        ):
            return 0, "Title does not match target roles"
        return 0, "No matching role or profile skill found"
    if facts["senior_title"]:
        return 0, "Requires more than the 0-1 year target or is a senior role"
    if facts["over_experience_target"]:
        if facts["eligibility_signal"]:
            return 0, (
                "Conflicting fresher/internship signal and experience requirement "
                "of 2+ years; manual review required"
            )
        return 0, "Requires more than the 0-1 year target or is a senior role"
    if not facts["eligibility_signal"]:
        if any(
            _has_term(get_text(job.get("title")).lower(), term)
            for term in ("junior",)
        ):
            return 0, (
                "Junior title alone does not confirm fresher or 0-1 year eligibility"
            )
        return 0, "No explicit fresher, internship, or 0-1 year eligibility signal"
    if not facts["location"]:
        return 0, "Outside Hyderabad/Telangana and India-eligible remote targets"
    if not _valid_job_url(job.get("url")):
        return 0, "Missing or invalid original job application URL"

    score = min(30, 15 + 5 * max(0, len(facts["role_hits"]) - 1))
    score += min(40, 8 * len(facts["skill_hits"]))
    if facts["fresher_hits"]:
        score += 15
    score += 25 if facts["location"] == "Hyderabad/Telangana" else 18

    reasons = []
    if facts["role_hits"]:
        reasons.append("role: " + ", ".join(facts["role_hits"][:3]))
    if facts["skill_hits"]:
        reasons.append("skills: " + ", ".join(facts["skill_hits"][:5]))
    if facts["internship"]:
        reasons.append("internship")
    elif facts["fresher_hits"]:
        reasons.append("entry-level signal")
    reasons.append(facts["location"])
    return min(100, score), "; ".join(reasons)


def canonicalize_url(url):
    """Remove fragments and common tracking parameters for stable duplicate keys."""
    if not url:
        return ""
    try:
        parts = urlsplit(get_text(url).strip())
    except ValueError:
        return ""
    query = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        urlencode(sorted(query)),
        "",
    ))


def _location_priority(job):
    location = get_text(job.get("location")).lower()
    if "hyderabad" in location:
        return 0
    if "telangana" in location:
        return 1
    if _target_location(job) == "Remote (India-eligible)":
        return 2
    return 3


def _posted_timestamp(value):
    text = get_text(value).strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return float(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (OverflowError, TypeError, ValueError):
            return None


def sort_jobs(jobs):
    """Sort target locations first, then score and newest known posting date."""
    jobs.sort(
        key=lambda job: (
            _location_priority(job),
            -job.get("match_score", 0),
            -(_posted_timestamp(job.get("posted_at")) or float("-inf")),
        )
    )
    return jobs


def job_key(job):
    canonical_url = canonicalize_url(get_text(job.get("url")))
    if canonical_url:
        return "url:" + canonical_url
    identity = "|".join(
        get_text(job.get(field)).strip().casefold()
        for field in ("title", "company", "location")
    )
    if identity.strip("|"):
        return "listing:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{job.get('source', 'unknown')}:{job.get('id', 'unknown')}"


def deduplicate_jobs(jobs):
    unique = {}
    for job in jobs:
        key = job_key(job)
        if key not in unique:
            unique[key] = dict(job)
            continue
        existing = unique[key]
        for field in (
            "title", "company", "location", "description", "experience", "url",
            "posted_at",
        ):
            if not existing.get(field) and job.get(field):
                existing[field] = job[field]
        for field in ("tags", "job_types", "required_skills"):
            existing[field] = sorted(
                set(existing.get(field, [])) | set(job.get(field, []))
            )
        existing["remote"] = bool(existing.get("remote") or job.get("remote"))
        existing["internship"] = bool(
            existing.get("internship") or job.get("internship")
        )
    return list(unique.values())


CSV_HEADER_ALIASES = {
    "title": "title",
    "job title": "title",
    "company": "company_name",
    "company name": "company_name",
    "employer": "company_name",
    "location": "location",
    "job location": "location",
    "description": "description",
    "job description": "description",
    "experience": "experience",
    "experience level": "experience",
    "experience required": "experience",
    "required experience": "experience",
    "job type": "job_type",
    "job types": "job_types",
    "employment type": "job_type",
    "posting date": "posted_at",
    "posted date": "posted_at",
    "date posted": "posted_at",
    "source": "source",
    "source name": "source",
    "url": "url",
    "job url": "url",
    "original url": "url",
    "original application url": "url",
    "application url": "url",
    "original job url": "url",
    "apply url": "url",
    "remote": "remote",
    "is remote": "remote",
    "tags": "tags",
    "skills": "tags",
}


def _normalize_csv_header(header):
    return re.sub(r"[^a-z0-9]+", " ", get_text(header).strip().lower()).strip()


def _canonical_csv_header(header):
    normalized = _normalize_csv_header(header)
    return CSV_HEADER_ALIASES.get(normalized)


def _split_csv_terms(value):
    if isinstance(value, str):
        return [term.strip() for term in re.split(r"[,;|]", value) if term.strip()]
    return _field_terms(value)


def preview_csv_import(path, threshold=35):
    """Read and score CSV listings without writing to the database or exports."""
    path = Path(path)
    candidates = []
    rejected = []
    rows_read = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source_file:
        first_content_line = ""
        while not first_content_line.strip():
            first_content_line = source_file.readline()
            if not first_content_line:
                break
        reader = csv.DictReader(chain((first_content_line,), source_file))
        if not reader.fieldnames:
            raise ValueError("CSV file is empty or has no header row.")
        canonical_headers = [_canonical_csv_header(header) for header in reader.fieldnames]
        if "title" not in canonical_headers or "url" not in canonical_headers:
            raise ValueError("CSV must include a job title column and an original URL column.")
        if len([header for header in canonical_headers if header]) != len(
            set(header for header in canonical_headers if header)
        ):
            raise ValueError("CSV contains multiple columns mapped to the same field.")

        for row_number, row in enumerate(reader, start=2):
            if row is None or not any(
                value is not None and str(value).strip()
                for value in row.values()
                if not isinstance(value, list)
            ):
                continue
            rows_read += 1
            title = ""
            if None in row:
                rejected.append({
                    "row": row_number,
                    "title": "(unknown title)",
                    "reason": "Row has more values than the CSV header.",
                })
                continue
            mapped = {}
            for header, value in row.items():
                canonical = _canonical_csv_header(header)
                if canonical:
                    mapped[canonical] = value
            title = get_text(mapped.get("title")).strip()
            if not title:
                rejected.append({
                    "row": row_number,
                    "title": "(missing title)",
                    "reason": "Job title is required.",
                })
                continue
            url = get_text(mapped.get("url")).strip()
            parsed_url = urlsplit(url)
            if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.netloc:
                rejected.append({
                    "row": row_number,
                    "title": title,
                    "reason": "A valid original http(s) application URL is required.",
                })
                continue

            mapped["source"] = get_text(mapped.get("source")).strip() or "CSV import"
            mapped["tags"] = _split_csv_terms(mapped.get("tags"))
            mapped["job_types"] = _split_csv_terms(mapped.get("job_types"))
            mapped["job_type"] = get_text(mapped.get("job_type")).strip()
            remote_value = get_text(mapped.get("remote")).strip().lower()
            mapped["remote"] = remote_value in {"1", "true", "yes", "y"}
            job = normalize_job(mapped, mapped["source"], remote=mapped["remote"])
            if job is None:
                rejected.append({
                    "row": row_number,
                    "title": title,
                    "reason": "CSV row could not be normalized.",
                })
                continue
            job["_import_row"] = row_number
            candidates.append(job)

    first_row_by_key = {}
    for job in candidates:
        key = job_key(job)
        first_row_by_key.setdefault(key, job["_import_row"])
    unique_jobs = deduplicate_jobs(candidates)
    for job in candidates:
        if job["_import_row"] != first_row_by_key[job_key(job)]:
            rejected.append({
                "row": job["_import_row"],
                "title": job["title"] or "(missing title)",
                "reason": "Duplicate listing in this CSV; first row retained.",
            })

    accepted = []
    for job in unique_jobs:
        score, reason = score_job(job)
        if score < threshold:
            rejected.append({
                "row": first_row_by_key[job_key(job)],
                "title": job["title"] or "(missing title)",
                "reason": reason if score == 0 else (
                    f"Score {score} is below the threshold of {threshold}."
                ),
            })
            continue
        job["match_score"] = score
        job["match_reason"] = reason
        accepted.append(job)

    sort_jobs(accepted)
    return {
        "path": str(path),
        "rows_read": rows_read,
        "accepted": accepted,
        "rejected": sorted(rejected, key=lambda item: item["row"]),
        "duplicates": sum(
            item["reason"].startswith("Duplicate listing")
            for item in rejected
        ),
    }


def print_csv_import_preview(report, saved=False):
    print(f"CSV import preview: {report['path']}")
    print(f"Non-empty rows read: {report['rows_read']}")
    print(f"Accepted matches: {len(report['accepted'])}")
    print(f"Rejected rows: {len(report['rejected'])}")
    for index, job in enumerate(report["accepted"], start=1):
        print(
            f"  {index}. [{job['match_score']}/100] {job['title']} — "
            f"{job.get('company') or 'Company not specified'}"
        )
        print(
            f"     {job.get('location') or 'Location not specified'} | "
            f"{job['experience_level']} | {job.get('posted_at') or 'Date not specified'}"
        )
        description = get_text(job.get("description")).replace("\n", " ").strip()
        if description:
            print(f"     Description: {description[:240]}")
        print(f"     Source: {job['source']}")
        print(f"     {job['url']}")
    if report["rejected"]:
        print("Rejected rows:")
        for item in report["rejected"]:
            print(
                f"  Row {item['row']}: {item['title']} — {item['reason']}"
            )
    if saved:
        print("Eligible rows saved to SQLite; no external actions were taken.")
    else:
        print("Preview only: no job records were saved.")


def build_diagnostic_report(raw_records, threshold=35, sample_limit=3):
    """Summarize match gates without drafting, storing, or exporting any jobs."""
    fetched = len(raw_records)
    normalized_jobs = []
    normalization_samples = []
    for source, item, remote in raw_records:
        job = normalize_job(item, source, remote=remote)
        if job is None:
            title = get_text(item.get("title")) if isinstance(item, dict) else ""
            normalization_samples.append({
                "title": title or "(missing title)",
                "reason": "Record is not a valid listing object",
            })
        else:
            normalized_jobs.append(job)

    jobs = deduplicate_jobs(normalized_jobs)
    facts_by_id = [match_facts(job) for job in jobs]
    scores_and_reasons = [score_job(job) for job in jobs]
    location_count = Counter(facts["location_type"] for facts in facts_by_id)
    independent = {
        "hyderabad_eligible": location_count["hyderabad"],
        "india_remote_eligible": location_count["india_remote"],
        "location_eligible": sum(bool(facts["location"]) for facts in facts_by_id),
        "fresher_experience_eligible": sum(
            facts["experience_eligible"] for facts in facts_by_id
        ),
        "relevant_roles": sum(facts["relevant"] for facts in facts_by_id),
        "score_at_or_above_threshold": sum(
            score >= threshold for score, _ in scores_and_reasons
        ),
        "valid_application_url": sum(
            _valid_job_url(job.get("url")) for job in jobs
        ),
        "location_and_experience": sum(
            bool(facts["location"]) and facts["experience_eligible"]
            for facts in facts_by_id
        ),
        "location_and_relevance": sum(
            bool(facts["location"]) and facts["relevant"]
            for facts in facts_by_id
        ),
        "experience_and_relevance": sum(
            facts["experience_eligible"] and facts["relevant"]
            for facts in facts_by_id
        ),
    }

    stage_rejections = {
        "normalization": normalization_samples,
        "location": [],
        "experience": [],
        "relevance": [],
        "url": [],
        "threshold": [],
    }
    cumulative = Counter()
    all_conditions = 0
    missing_location = 0
    missing_experience = 0
    missing_description = 0
    for job, facts, (score, score_reason) in zip(
        jobs, facts_by_id, scores_and_reasons
    ):
        title = get_text(job.get("title")).strip() or "(missing title)"
        description = get_text(job.get("description"))
        if not get_text(job.get("location")).strip():
            missing_location += 1
        experience_text = f"{title} {description} {' '.join(job.get('tags', []))}"
        bounds = _experience_bounds(experience_text)
        has_experience_signal = bool(facts["fresher_hits"]) or bounds is not None
        if not has_experience_signal:
            missing_experience += 1
        if not description.strip():
            missing_description += 1

        if not facts["location"]:
            reason = (
                "Location is missing"
                if not get_text(job.get("location")).strip()
                else "Not Hyderabad/Telangana or India-eligible remote"
            )
            stage_rejections["location"].append({"title": title, "reason": reason})
            continue
        cumulative["location"] += 1

        if not facts["experience_eligible"]:
            if facts["senior_title"]:
                reason = "Senior/lead-level title"
            elif facts["over_experience_target"]:
                bounds = facts["experience_bounds"]
                reason = (
                    f"Requires {bounds[0]}+ years"
                    if bounds[0] == bounds[1]
                    else f"Requires {bounds[0]}-{bounds[1]} years"
                )
            else:
                reason = "No explicit fresher, internship, or 0-1 year signal"
            stage_rejections["experience"].append({"title": title, "reason": reason})
            continue
        cumulative["experience"] += 1

        if not facts["relevant"]:
            reason = score_reason
            stage_rejections["relevance"].append({"title": title, "reason": reason})
            continue
        cumulative["relevance"] += 1

        if not _valid_job_url(job.get("url")):
            stage_rejections["url"].append({
                "title": title,
                "reason": "Missing or invalid original job application URL",
            })
            continue
        cumulative["url"] += 1

        if score < threshold:
            stage_rejections["threshold"].append({
                "title": title,
                "reason": f"Score {score} is below threshold {threshold}",
            })
            continue
        cumulative["final"] += 1
        all_conditions += 1

    def representative_samples(samples):
        selected = []
        seen_reasons = set()
        for sample in samples:
            if sample["reason"] not in seen_reasons:
                selected.append(sample)
                seen_reasons.add(sample["reason"])
                if len(selected) == sample_limit:
                    return selected
        for sample in samples:
            if sample not in selected:
                selected.append(sample)
                if len(selected) == sample_limit:
                    break
        return selected

    return {
        "fetched": fetched,
        "normalized": len(normalized_jobs),
        "deduplicated": len(jobs),
        "independent": independent,
        "pipeline": {
            "after_location": cumulative["location"],
            "after_experience": cumulative["experience"],
            "after_relevance": cumulative["relevance"],
            "after_valid_url": cumulative["url"],
            "final_matches": cumulative["final"],
            "all_conditions": all_conditions,
        },
        "missing": {
            "location": missing_location,
            "experience_information": missing_experience,
            "description": missing_description,
        },
        "rejections": {
            stage: {
                "count": len(samples),
                "samples": representative_samples(samples),
            }
            for stage, samples in stage_rejections.items()
        },
    }


def print_diagnostic_report(report):
    print("Diagnostic report (read-only; no drafts, database writes, or exports).")
    print(f"Fetched: {report['fetched']}")
    print(f"Normalized: {report['normalized']}")
    print(f"Unique after duplicate detection: {report['deduplicated']}")
    print("Independent conditions:")
    for label, key in (
        ("Hyderabad/Telangana eligible", "hyderabad_eligible"),
        ("India-eligible remote", "india_remote_eligible"),
        ("Either target location", "location_eligible"),
        ("Fresher/experience eligible", "fresher_experience_eligible"),
        ("Relevant software roles", "relevant_roles"),
    ):
        print(f"  {label}: {report['independent'][key]}")
    print("Pairwise overlap (before score threshold):")
    for label, key in (
        ("Location and experience", "location_and_experience"),
        ("Location and role relevance", "location_and_relevance"),
        ("Experience and role relevance", "experience_and_relevance"),
    ):
        print(f"  {label}: {report['independent'][key]}")
    print(
        "  Score at or above configured threshold: "
        f"{report['independent']['score_at_or_above_threshold']}"
    )
    print("Pipeline progression (after normalization/deduplication):")
    print(f"  Location eligible: {report['pipeline']['after_location']}")
    print(f"  After fresher/experience filter: {report['pipeline']['after_experience']}")
    print(f"  After software-role relevance: {report['pipeline']['after_relevance']}")
    print(f"  With valid original job URL: {report['pipeline']['after_valid_url']}")
    print(f"  Final matches: {report['pipeline']['final_matches']}")
    print(f"  Pass all conditions together: {report['pipeline']['all_conditions']}")
    print("Missing listing information:")
    print(f"  Location: {report['missing']['location']}")
    print(f"  Experience information: {report['missing']['experience_information']}")
    print(f"  Job description: {report['missing']['description']}")
    print("Representative first-failure rejections:")
    for stage, label in (
        ("normalization", "Normalization"),
        ("location", "Location"),
        ("experience", "Fresher/experience"),
        ("relevance", "Role relevance"),
        ("url", "Original job URL"),
        ("threshold", "Score threshold"),
    ):
        rejection = report["rejections"][stage]
        print(f"  {label}: {rejection['count']}")
        for sample in rejection["samples"]:
            print(f"    - {sample['title']}: {sample['reason']}")


def make_email_draft(job):
    company = job.get("company") or "your team"
    title = job.get("title") or "the role"
    subject = f"Application interest: {title} | Fresher Python Developer"
    degree = os.getenv("CANDIDATE_DEGREE", "").strip()
    github_url = os.getenv("CANDIDATE_GITHUB_URL", "").strip()
    background = (
        f"My background includes Python, SQL, Django REST Framework, Git, Angular, "
        f"and my Optimal Guide DSA project."
    )
    if degree:
        background = f"I hold {degree}. " + background
    body = (
        f"Hello Hiring Team,\n\n"
        f"I'm interested in the {title} opportunity at {company}. {background} "
        f"I would appreciate your consideration for this role.\n\n"
        f"My resume: [attach your resume]\n"
        + (f"GitHub: {github_url}\n" if github_url else "")
        + f"\nIf the position is still open, I'd be grateful for the opportunity to "
        f"discuss how I could contribute. Thank you for your time.\n\n"
        f"Regards,\n[Your name]\n"
    )
    return subject, body


def gemini_draft(job):
    """Optional Gemini email personalization. Falls back safely if not configured."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return make_email_draft(job)
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        candidate_degree = os.getenv("CANDIDATE_DEGREE", "").strip()
        candidate_github = os.getenv("CANDIDATE_GITHUB_URL", "").strip()
        prompt = f"""
Write a concise, truthful application email using only these candidate facts:
skills Python, SQL, Django REST Framework, Git, Angular, and the Optimal Guide
DSA project.
Candidate degree (include only if present): {candidate_degree}
Candidate GitHub URL (include only if present): {candidate_github}
Do not invent experience, achievements, contact details, or qualifications. Ask
about the role politely. Return exactly JSON with keys subject and body, no markdown.

Job title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Job description excerpt: {job.get('description', '')[:2500]}
Job URL: {job.get('url')}
"""
        response = client.models.generate_content(model=model, contents=prompt)
        text = response.text or ""
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
        data = json.loads(text)
        return str(data["subject"]), str(data["body"])
    except Exception as exc:
        LOGGER.warning("Gemini drafting unavailable; using template: %s", exc)
        return make_email_draft(job)


def store_job(job, score, reason, subject, draft):
    key = job_key(job)
    seen_at = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        # Reuse an older source:id key when the URL is already in an existing DB.
        canonical = canonicalize_url(get_text(job.get("url")))
        if canonical:
            for stored_key, stored_url in con.execute(
                "SELECT job_key, job_url FROM jobs WHERE job_url IS NOT NULL"
            ):
                if canonicalize_url(stored_url) == canonical:
                    key = stored_key
                    break
        con.execute("""
            INSERT INTO jobs (
                job_key, title, company, location, job_url, source, posted_at,
                required_skills, experience_level, match_score, match_reason,
                contact_name, public_contact_email,
                public_contact_url, email_subject, email_draft, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_key) DO UPDATE SET
                title=excluded.title,
                company=excluded.company,
                location=excluded.location,
                job_url=excluded.job_url,
                source=excluded.source,
                posted_at=excluded.posted_at,
                required_skills=excluded.required_skills,
                experience_level=excluded.experience_level,
                match_score=excluded.match_score,
                match_reason=excluded.match_reason,
                contact_name=excluded.contact_name,
                public_contact_email=excluded.public_contact_email,
                public_contact_url=excluded.public_contact_url,
                email_subject=excluded.email_subject,
                email_draft=excluded.email_draft,
                first_seen=COALESCE(jobs.first_seen, excluded.first_seen),
                last_seen=excluded.last_seen
        """, (
            key, job["title"], job["company"], job["location"], job["url"],
            job["source"], job["posted_at"],
            "; ".join(job.get("required_skills", [])),
            job.get("experience_level", "Not specified"),
            score, reason, "", "", "", subject, draft,
            seen_at,
            seen_at,
        ))


def export_csv(jobs, path=CSV_PATH):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for job in jobs:
            writer.writerow({
                "title": get_text(job.get("title")),
                "company": get_text(job.get("company")),
                "location": get_text(job.get("location")),
                "url": get_text(job.get("url")),
                "required_skills": "; ".join(job.get("required_skills", [])),
                "experience_level": get_text(job.get("experience_level")) or "Not specified",
                "posted_at": get_text(job.get("posted_at")),
                "match_score": job.get("match_score", 0),
                "source": get_text(job.get("source")),
            })


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find fresher and junior software jobs.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--diagnose",
        action="store_true",
        help="fetch listings and report read-only matching diagnostics",
    )
    mode.add_argument(
        "--import-csv",
        metavar="PATH",
        help="preview listings from a local CSV file without saving them",
    )
    parser.add_argument(
        "--save-import",
        action="store_true",
        help="save accepted CSV matches to SQLite (requires --import-csv)",
    )
    args = parser.parse_args(argv)
    if args.save_import and not args.import_csv:
        parser.error("--save-import requires --import-csv PATH")
    configure_logging()

    if args.diagnose:
        LOGGER.info("Starting read-only source diagnostics.")
        print("Fetching public job listings for read-only diagnostics...")
        raw_records = [
            ("Arbeitnow", item, False) for item in fetch_arbeitnow_records()
        ]
        raw_records.extend(
            ("Remotive", item, True) for item in fetch_remotive_records()
        )
        raw_records.extend(
            ("Jobicy", item, True) for item in fetch_jobicy_records()
        )
        print_diagnostic_report(build_diagnostic_report(raw_records))
        return

    if args.import_csv:
        try:
            report = preview_csv_import(args.import_csv)
        except (OSError, UnicodeDecodeError, csv.Error, ValueError) as exc:
            parser.error(f"Could not import CSV: {exc}")
        LOGGER.info(
            "CSV preview completed: %d accepted and %d rejected.",
            len(report["accepted"]),
            len(report["rejected"]),
        )
        if args.save_import:
            init_db()
            LOGGER.info("Starting public job-source search.")
            for job in report["accepted"]:
                subject, draft = make_email_draft(job)
                store_job(
                    job, job["match_score"], job["match_reason"], subject, draft
                )
        print_csv_import_preview(report, saved=args.save_import)
        if args.save_import:
            print(f"Saved {len(report['accepted'])} eligible CSV listings to {DB_PATH.name}.")
        return

    init_db()
    print("Fetching public job listings. This can take a few seconds...")
    jobs = deduplicate_jobs(
        fetch_arbeitnow() + fetch_remotive() + fetch_jobicy()
    )
    print(f"Fetched {len(jobs)} listings.")
    LOGGER.info("Fetched %d unique normalized listings.", len(jobs))

    matches = []
    for job in jobs:
        score, reason = score_job(job)
        if score < 35:
            continue
        subject, draft = gemini_draft(job)
        store_job(job, score, reason, subject, draft)
        job["match_score"] = score
        job["match_reason"] = reason
        job["email_subject"] = subject
        job["email_draft"] = draft
        matches.append(job)

    sort_jobs(matches)
    OUTPUT_PATH.write_text(json.dumps(matches, indent=2, ensure_ascii=False), encoding="utf-8")
    export_csv(matches)

    print(
        f"\nSaved {len(matches)} matching jobs to {DB_PATH.name}, "
        f"{OUTPUT_PATH.name}, and {CSV_PATH.name}."
    )
    LOGGER.info("Saved %d matching listings.", len(matches))
    print("No emails were sent and no applications were submitted.")
    if not matches:
        print(
            "No matches this run. Use --diagnose to inspect coverage; "
            "do not relax eligibility filters without evidence."
        )
        return

    for index, job in enumerate(matches[:20], start=1):
        print(f"\n{index}. [{job['match_score']}/100] {job['title']} — {job['company']}")
        print(f"   Location: {job['location'] or 'Not specified'} | Source: {job['source']}")
        print(f"   Match: {job['match_reason']}")
        print(f"   Job: {job['url']}")

    print("\nOpen matched_jobs.json to review the drafts. Verify each job is active and eligible.")
    print("Recruiter contact fields stay blank unless the source listing publishes them.")


if __name__ == "__main__":
    main()
