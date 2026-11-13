import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import job_hunter


def listing(**overrides):
    job = {
        "id": "job-1",
        "title": "Junior Python Developer",
        "company": "Example Co",
        "location": "Hyderabad, Telangana",
        "description": (
            "Fresher role using Python, SQL, Django REST Framework, Git, and Angular."
        ),
        "url": "https://jobs.example/role/1",
        "posted_at": "2026-10-01",
        "required_skills": ["python", "sql"],
        "experience_level": "Fresher / entry-level",
        "remote": False,
        "source": "Example",
        "contact_name": "",
        "public_contact_email": "",
        "public_contact_url": "",
    }
    job.update(overrides)
    return job


class JobHunterTests(unittest.TestCase):
    def write_import_csv(self, directory, rows, headers=None):
        path = Path(directory) / "jobs.csv"
        if headers is None:
            headers = [
                "Job Title", "Company", "Location", "Description", "Experience",
                "Job Type", "Posting Date", "Source",
                "Original Application URL", "Remote", "Tags",
            ]
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def import_row(self, **overrides):
        row = {
            "Job Title": "Junior Python Developer",
            "Company": "Example Co",
            "Location": "Hyderabad, Telangana",
            "Description": "Fresher role working with Python and Django.",
            "Experience": "0-1 year",
            "Job Type": "Full-time",
            "Posting Date": "2026-10-01",
            "Source": "Permitted board",
            "Original Application URL": "https://jobs.example/role/1",
            "Remote": "no",
            "Tags": "Python; Django REST Framework; SQL",
        }
        row.update(overrides)
        return row

    def test_csv_import_previews_valid_normalized_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_import_csv(directory, [self.import_row()])
            report = job_hunter.preview_csv_import(path)
        self.assertEqual(report["rows_read"], 1)
        self.assertEqual(len(report["accepted"]), 1)
        job = report["accepted"][0]
        self.assertEqual(job["title"], "Junior Python Developer")
        self.assertEqual(job["experience"], "0-1 year")
        self.assertEqual(job["experience_level"], "0-1 years")
        self.assertEqual(job["source"], "Permitted board")
        self.assertEqual(job["url"], "https://jobs.example/role/1")
        self.assertGreaterEqual(job["match_score"], 35)
        self.assertEqual(report["rejected"], [])

    def test_csv_import_skips_leading_blank_crlf_before_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leading_blank.csv"
            path.write_bytes(
                b"\r\n"
                b"title,company,location,description,experience,job_type,source,application_url\r\n"
                b"Junior Python Developer,Example Company,Hyderabad,"
                b'"Fresher Python role\r\nwith a quoted continuation",0-1 years,'
                b"Full-time,Manual,https://example.com/jobs/1\r\n"
                b"Senior Python Developer,Example Company,Hyderabad,"
                b"Python development,5+ years,Full-time,Manual,"
                b"https://example.com/jobs/2\r\n"
                b"Python Developer Intern,Example Company,Bengaluru,"
                b"Python internship,Internship,Internship,Manual,"
                b"https://example.com/jobs/3\r\n"
            )
            report = job_hunter.preview_csv_import(path)

        self.assertEqual(report["rows_read"], 3)
        self.assertEqual(len(report["accepted"]), 1)
        self.assertEqual(len(report["rejected"]), 2)
        self.assertIn(
            "with a quoted continuation",
            report["accepted"][0]["description"],
        )
        self.assertIn("0-1 years", report["accepted"][0]["experience"])
        self.assertIn("0-1 year", report["rejected"][0]["reason"])
        self.assertIn("Outside", report["rejected"][1]["reason"])

    def test_csv_import_rejects_missing_title_url_and_fresher_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                self.import_row(**{"Job Title": "", "Original Application URL": ""}),
                self.import_row(**{"Original Application URL": "javascript:alert(1)"}),
                self.import_row(
                    **{
                        "Job Title": "Python Developer",
                        "Experience": "",
                        "Description": "Build Python backend services.",
                    }
                ),
            ]
            path = self.write_import_csv(directory, rows)
            report = job_hunter.preview_csv_import(path)
        self.assertEqual(len(report["accepted"]), 0)
        self.assertEqual(len(report["rejected"]), 3)
        self.assertIn("Job title is required", report["rejected"][0]["reason"])
        self.assertIn("valid original", report["rejected"][1]["reason"])
        self.assertIn("No explicit", report["rejected"][2]["reason"])

    def test_csv_import_rejects_malformed_row_and_missing_required_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "malformed.csv"
            malformed.write_text(
                "title,url,location,description,experience\n"
                "Python Developer,https://jobs.example/x,Hyderabad,"
                "Fresher Python role,0-1 year,unexpected extra\n",
                encoding="utf-8",
            )
            report = job_hunter.preview_csv_import(malformed)
            no_url = self.write_import_csv(
                directory,
                [],
                headers=["title", "company", "location", "description"],
            )
            with self.assertRaisesRegex(ValueError, "original URL"):
                job_hunter.preview_csv_import(no_url)
        self.assertEqual(report["accepted"], [])
        self.assertEqual(report["rejected"][0]["row"], 2)
        self.assertIn("more values", report["rejected"][0]["reason"])

    def test_csv_import_deduplicates_urls_and_prioritizes_hyderabad(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                self.import_row(
                    **{
                        "Job Title": "Junior Python Developer",
                        "Original Application URL":
                            "https://jobs.example/role/remote",
                        "Location": "India",
                        "Remote": "yes",
                    }
                ),
                self.import_row(
                    **{
                        "Original Application URL":
                            "https://jobs.example/role/1?utm_source=board",
                    }
                ),
                self.import_row(
                    **{
                        "Original Application URL":
                            "https://JOBS.example/role/1#apply",
                    }
                ),
            ]
            path = self.write_import_csv(directory, rows)
            report = job_hunter.preview_csv_import(path)
        self.assertEqual(len(report["accepted"]), 2)
        self.assertEqual(report["accepted"][0]["location"], "Hyderabad, Telangana")
        self.assertEqual(report["duplicates"], 1)
        self.assertIn("Duplicate listing", report["rejected"][0]["reason"])

    @patch("job_hunter.init_db")
    @patch("job_hunter.store_job")
    def test_csv_import_preview_is_read_only_and_save_requires_flag(
        self, store_job, init_db
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_import_csv(directory, [self.import_row()])
            with patch("builtins.print"):
                job_hunter.main(["--import-csv", str(path)])
            init_db.assert_not_called()
            store_job.assert_not_called()

            with patch("job_hunter.DB_PATH", Path(directory) / "import.db"):
                with patch("builtins.print"):
                    job_hunter.main([
                        "--import-csv", str(path), "--save-import"
                    ])
            init_db.assert_called_once()
            store_job.assert_called_once()

    def test_csv_import_maps_experience_and_internship_signals_into_shared_matcher(self):
        with tempfile.TemporaryDirectory() as directory:
            internship = self.import_row(
                **{
                    "Job Title": "Software Engineer",
                    "Experience": "",
                    "Job Type": "Internship",
                    "Description": "Work on Python backend services.",
                    "Location": "India",
                    "Remote": "yes",
                    "Original Application URL": "https://jobs.example/internship",
                }
            )
            over_one_year = self.import_row(
                **{
                    "Experience": "2+ years",
                    "Description": "Python and Django backend developer.",
                    "Original Application URL": "https://jobs.example/2-years",
                }
            )
            path = self.write_import_csv(directory, [internship, over_one_year])
            report = job_hunter.preview_csv_import(path)
        self.assertEqual(len(report["accepted"]), 1)
        self.assertEqual(report["accepted"][0]["experience_level"], "Internship")
        self.assertEqual(len(report["rejected"]), 1)
        self.assertIn("0-1 year", report["rejected"][0]["reason"])

    def diagnostic_records(self):
        return [
            ("Example", {
                "title": "Junior Python Developer",
                "location": "Hyderabad",
                "description": "Fresher role using Python and SQL.",
                "url": "https://jobs.example/diagnostic-1",
            }, False),
            ("Example", {
                "title": "Software Engineer",
                "location": "India",
                "description": "Build Python services.",
                "job_type": "Internship",
                "url": "https://jobs.example/diagnostic-2",
            }, True),
            ("Example", {
                "title": "Junior Python Developer",
                "location": "Berlin",
                "description": "Fresher role using Python.",
                "url": "https://jobs.example/diagnostic-3",
            }, False),
            ("Example", {
                "title": "Python Developer",
                "location": "Hyderabad",
                "description": "Requires 2 years of experience with Python.",
                "url": "https://jobs.example/diagnostic-4",
            }, False),
            ("Example", {
                "title": "HR Assistant",
                "location": "Hyderabad",
                "description": "Fresher opportunity. Python is a bonus.",
                "url": "https://jobs.example/diagnostic-5",
            }, False),
            ("Example", None, False),
            ("Example", {
                "title": "Python Developer",
                "location": "Hyderabad",
                "description": "Build Python backend services.",
                "url": "https://jobs.example/diagnostic-7",
            }, False),
            ("Example", {
                "title": "Junior Django Developer",
                "description": "",
                "url": "https://jobs.example/diagnostic-8",
            }, False),
        ]

    def test_diagnostic_reports_independent_and_sequential_pipeline_counts(self):
        report = job_hunter.build_diagnostic_report(self.diagnostic_records())
        self.assertEqual(report["fetched"], 8)
        self.assertEqual(report["normalized"], 7)
        self.assertEqual(report["deduplicated"], 7)
        self.assertEqual(report["independent"]["hyderabad_eligible"], 4)
        self.assertEqual(report["independent"]["india_remote_eligible"], 1)
        self.assertEqual(report["independent"]["location_eligible"], 5)
        self.assertEqual(report["independent"]["fresher_experience_eligible"], 4)
        self.assertEqual(report["independent"]["relevant_roles"], 6)
        self.assertEqual(report["pipeline"]["after_location"], 5)
        self.assertEqual(report["pipeline"]["after_experience"], 3)
        self.assertEqual(report["pipeline"]["after_relevance"], 2)
        self.assertEqual(report["pipeline"]["after_valid_url"], 2)
        self.assertEqual(report["pipeline"]["final_matches"], 2)
        self.assertEqual(report["pipeline"]["all_conditions"], 2)

    def test_diagnostic_reports_missing_fields_and_rejection_samples(self):
        report = job_hunter.build_diagnostic_report(self.diagnostic_records())
        self.assertEqual(report["missing"]["location"], 1)
        self.assertEqual(report["missing"]["experience_information"], 2)
        self.assertEqual(report["missing"]["description"], 1)
        self.assertEqual(report["rejections"]["normalization"]["count"], 1)
        self.assertEqual(report["rejections"]["location"]["count"], 2)
        self.assertEqual(report["rejections"]["experience"]["count"], 2)
        self.assertEqual(report["rejections"]["relevance"]["count"], 1)
        self.assertEqual(report["rejections"]["threshold"]["count"], 0)
        self.assertLessEqual(len(report["rejections"]["location"]["samples"]), 3)
        self.assertEqual(
            report["rejections"]["location"]["samples"][0]["title"],
            "Junior Python Developer",
        )

    def test_diagnostic_reports_invalid_original_url_as_its_own_gate(self):
        records = [(
            "Example",
            {
                "title": "Python Developer",
                "location": "Hyderabad",
                "description": "Entry-level Python backend role.",
                "url": "",
            },
            False,
        )]
        report = job_hunter.build_diagnostic_report(records)
        self.assertEqual(report["pipeline"]["after_relevance"], 1)
        self.assertEqual(report["pipeline"]["after_valid_url"], 0)
        self.assertEqual(report["rejections"]["url"]["count"], 1)
        self.assertEqual(report["pipeline"]["final_matches"], 0)

    @patch("job_hunter.fetch_remotive_records")
    @patch("job_hunter.fetch_jobicy_records")
    @patch("job_hunter.fetch_arbeitnow_records")
    @patch("job_hunter.init_db")
    @patch("job_hunter.store_job")
    @patch("job_hunter.gemini_draft")
    @patch("job_hunter.configure_logging")
    def test_diagnose_mode_fetches_and_reports_without_writes_or_drafts(
        self, configure_logging, gemini_draft, store_job, init_db,
        fetch_arbeitnow_records, fetch_jobicy_records, fetch_remotive_records,
    ):
        fetch_arbeitnow_records.return_value = [{
            "title": "Junior Python Developer",
            "location": "Hyderabad",
            "description": "Fresher role using Python.",
            "url": "https://jobs.example/diagnose-only",
        }]
        fetch_remotive_records.return_value = []
        fetch_jobicy_records.return_value = []
        with patch("builtins.print") as output:
            job_hunter.main(["--diagnose"])
        init_db.assert_not_called()
        store_job.assert_not_called()
        gemini_draft.assert_not_called()
        configure_logging.assert_called_once()
        printed = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("read-only", printed)
        self.assertIn("Final matches: 1", printed)

    def test_scores_hyderabad_fresher_job(self):
        score, reason = job_hunter.score_job(listing())
        self.assertGreaterEqual(score, 35)
        self.assertIn("Hyderabad/Telangana", reason)
        self.assertIn("entry-level signal", reason)

    def test_accepts_india_eligible_remote_and_rejects_unscoped_remote(self):
        india_score, _ = job_hunter.score_job(
            listing(location="India", remote=True)
        )
        worldwide_score, _ = job_hunter.score_job(
            listing(location="Worldwide", remote=True)
        )
        outside_score, reason = job_hunter.score_job(
            listing(location="United States", remote=True)
        )
        self.assertGreaterEqual(india_score, 35)
        self.assertGreaterEqual(worldwide_score, 35)
        self.assertEqual(outside_score, 0)
        self.assertIn("Outside", reason)
        for location in (
            "Anywhere",
            "Anywhere in Europe",
            "Worldwide except India",
            "India not eligible",
        ):
            with self.subTest(location=location):
                score, _ = job_hunter.score_job(
                    listing(location=location, remote=True)
                )
                self.assertEqual(score, 0)

    def test_filters_substantial_experience_and_unrelated_roles(self):
        experience_score, experience_reason = job_hunter.score_job(
            listing(description="Requires 4+ years of professional experience.")
        )
        unrelated_score, unrelated_reason = job_hunter.score_job(
            listing(title="Marketing Manager", description="Python is a bonus.")
        )
        self.assertEqual(experience_score, 0)
        self.assertIn("0-1 year", experience_reason)
        self.assertEqual(unrelated_score, 0)
        self.assertIn("Title", unrelated_reason)

    def test_keeps_one_year_requirement_and_reports_the_level(self):
        job = job_hunter.normalize_job({
            "title": "Junior Python Developer",
            "company_name": "Example",
            "location": "Hyderabad",
            "description": "Requires 1 year of experience with Python.",
            "url": "https://jobs.example/two-years",
        }, "Example")
        if job is None:
            self.fail("Expected a valid listing to be normalized.")
        self.assertEqual(job["experience_level"], "1 years")
        score, _ = job_hunter.score_job(job)
        self.assertGreater(score, 0)

    def test_one_year_requirement_is_explicit_fresher_eligibility(self):
        job = listing(
            title="Python Developer",
            description="Python backend role requiring 1 year of experience.",
        )
        score, _ = job_hunter.score_job(job)
        self.assertGreaterEqual(score, 35)

    def test_junior_title_alone_does_not_prove_fresher_eligibility(self):
        job = listing(
            title="Junior Python Developer",
            description="Build backend services with Python and Django.",
        )
        score, reason = job_hunter.score_job(job)
        self.assertEqual(score, 0)
        self.assertIn("Junior title alone", reason)

    def test_conflicting_fresher_signal_and_two_year_requirement_is_review_only(self):
        job = listing(
            description=(
                "Fresher opportunity, but requires 2 years of experience with Python."
            )
        )
        score, reason = job_hunter.score_job(job)
        self.assertEqual(score, 0)
        self.assertIn("Conflicting", reason)
        self.assertIn("manual review", reason)

    def test_rejects_experience_above_one_year_and_senior_titles(self):
        two_year_job = listing(description="Requires 2 years of experience.")
        range_job = listing(
            description="Requires 1–2 years' experience with Django."
        )
        senior_job = listing(title="Senior Django Developer")
        two_year_score, two_year_reason = job_hunter.score_job(two_year_job)
        range_score, range_reason = job_hunter.score_job(range_job)
        senior_score, senior_reason = job_hunter.score_job(senior_job)
        self.assertEqual(two_year_score, 0)
        self.assertIn("0-1 year", two_year_reason)
        self.assertEqual(range_score, 0)
        self.assertIn("0-1 year", range_reason)
        self.assertEqual(senior_score, 0)
        self.assertIn("senior", senior_reason)

    def test_rejects_missing_or_invalid_original_application_url(self):
        for url in ("", "javascript:alert(1)", "https://"):
            with self.subTest(url=url):
                score, reason = job_hunter.score_job(listing(url=url))
                self.assertEqual(score, 0)
                self.assertIn("original job application URL", reason)

    def test_results_prioritize_hyderabad_then_telangana_then_remote(self):
        jobs = [
            listing(location="India", remote=True, match_score=100),
            listing(location="Warangal, Telangana", match_score=60),
            listing(location="Hyderabad", match_score=40),
        ]
        job_hunter.sort_jobs(jobs)
        self.assertEqual(
            [job["location"] for job in jobs],
            ["Hyderabad", "Warangal, Telangana", "India"],
        )

    def test_results_use_newest_posting_date_after_location_and_score(self):
        jobs = [
            listing(posted_at="2026-10-01", match_score=70),
            listing(posted_at="2026-10-03", match_score=70),
        ]
        job_hunter.sort_jobs(jobs)
        self.assertEqual(jobs[0]["posted_at"], "2026-10-03")

    def test_normalizes_skill_tags_for_matching(self):
        job = job_hunter.normalize_job({
            "title": "Junior Backend Developer",
            "company_name": "Example",
            "location": "Hyderabad",
            "description": "Entry-level role.",
            "tags": ["Python", "Django REST Framework", "Git"],
            "url": "https://jobs.example/tagged-role",
        }, "Example")
        if job is None:
            self.fail("Expected a valid listing to be normalized.")
        self.assertEqual(
            job["required_skills"],
            ["python", "django rest framework", "django", "git"],
        )
        score, reason = job_hunter.score_job(job)
        self.assertGreaterEqual(score, 35)
        self.assertIn("django", reason)

    def test_job_type_internship_is_a_positive_entry_level_signal(self):
        job = job_hunter.normalize_job({
            "title": "Software Engineer",
            "company_name": "Example",
            "location": "Hyderabad",
            "description": "Work on Python services with our engineering team.",
            "job_types": ["Internship"],
            "url": "https://jobs.example/intern",
        }, "Example")
        if job is None:
            self.fail("Expected a valid listing to be normalized.")
        self.assertTrue(job["internship"])
        self.assertEqual(job["experience_level"], "Internship")
        score, reason = job_hunter.score_job(job)
        self.assertGreaterEqual(score, 35)
        self.assertIn("internship", reason)

    def test_handles_malformed_tags_and_job_type_fields_defensively(self):
        job = job_hunter.normalize_job({
            "title": "Python Developer",
            "location": "Hyderabad",
            "description": "Junior role",
            "tags": {"language": "Python", "database": ["SQL"]},
            "job_types": None,
            "job_type": {"kind": "Internship"},
        }, "Example")
        if job is None:
            self.fail("Expected a valid listing to be normalized.")
        self.assertIn("python", job["required_skills"])
        self.assertIn("sql", job["required_skills"])
        self.assertTrue(job["internship"])

    def test_missing_experience_stays_unspecified_without_entry_level_bonus(self):
        job = job_hunter.normalize_job({
            "title": "Python Developer",
            "company_name": "Example",
            "location": "Hyderabad",
            "description": "Build Python backend services with Django.",
            "url": "https://jobs.example/no-exp-info",
        }, "Example")
        if job is None:
            self.fail("Expected a valid listing to be normalized.")
        self.assertEqual(job["experience_level"], "Not specified")
        score, reason = job_hunter.score_job(job)
        self.assertEqual(score, 0)
        self.assertIn("No explicit", reason)
        self.assertNotIn("entry-level signal", reason)

    def test_deduplicates_tracking_variants_of_same_listing(self):
        first = listing(url="https://jobs.example/role/1?utm_source=board")
        second = listing(
            source="Other",
            url="https://JOBS.example/role/1/#apply",
            posted_at="",
        )
        jobs = job_hunter.deduplicate_jobs([first, second])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["posted_at"], "2026-10-01")

    def test_migrates_old_database_columns_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            old_database = Path(directory) / "old.db"
            with closing(sqlite3.connect(old_database)) as connection:
                with connection:
                    connection.execute("""
                        CREATE TABLE jobs (
                            job_key TEXT PRIMARY KEY, title TEXT, company TEXT,
                            location TEXT, job_url TEXT, source TEXT, posted_at TEXT,
                            match_score INTEGER, match_reason TEXT, contact_name TEXT,
                            public_contact_email TEXT, public_contact_url TEXT,
                            email_subject TEXT, email_draft TEXT, status TEXT DEFAULT 'new',
                            first_seen TEXT
                        )
                    """)
                    connection.execute(
                        "INSERT INTO jobs (job_key, title) VALUES ('old-key', 'Old role')"
                    )
            with patch.object(job_hunter, "DB_PATH", old_database):
                job_hunter.init_db()
                with closing(sqlite3.connect(old_database)) as connection:
                    columns = {
                        row[1] for row in connection.execute("PRAGMA table_info(jobs)")
                    }
                    old_title = connection.execute(
                        "SELECT title FROM jobs WHERE job_key='old-key'"
                    ).fetchone()[0]
            self.assertIn("required_skills", columns)
            self.assertIn("experience_level", columns)
            self.assertIn("last_seen", columns)
            self.assertEqual(old_title, "Old role")

    def test_store_job_deduplicates_existing_legacy_url_and_keeps_contacts_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.db"
            with patch.object(job_hunter, "DB_PATH", database):
                job_hunter.init_db()
                job = listing()
                with closing(sqlite3.connect(database)) as connection:
                    with connection:
                        connection.execute("""
                            INSERT INTO jobs (job_key, job_url, title)
                            VALUES ('Example:job-1', ?, 'Older title')
                        """, (job["url"],))
                job_hunter.store_job(job, 80, "match", "subject", "draft")
                with closing(sqlite3.connect(database)) as connection:
                    first_seen, last_seen = connection.execute(
                        "SELECT first_seen, last_seen FROM jobs"
                    ).fetchone()
                job_hunter.store_job(job, 80, "match", "subject", "draft")
                with closing(sqlite3.connect(database)) as connection:
                    rows = connection.execute(
                        "SELECT job_key, title, required_skills, experience_level, "
                        "contact_name, public_contact_email, first_seen, last_seen "
                        "FROM jobs"
                    ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "Example:job-1")
            self.assertEqual(rows[0][1], "Junior Python Developer")
            self.assertIn("python", rows[0][2])
            self.assertEqual(rows[0][3], "Fresher / entry-level")
            self.assertEqual(rows[0][4:6], ("", ""))
            self.assertEqual(rows[0][6], first_seen)
            self.assertGreaterEqual(rows[0][7], last_seen)
            self.assertIsNotNone(rows[0][7])

    def test_exports_requested_normalized_fields_to_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matches.csv"
            job = listing(match_score=82)
            job_hunter.export_csv([job], path)
            with path.open(encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                row = next(reader)
            self.assertEqual(
                reader.fieldnames,
                list(job_hunter.CSV_FIELDS),
            )
            self.assertEqual(row["title"], job["title"])
            self.assertEqual(row["url"], job["url"])
            self.assertEqual(row["required_skills"], "python; sql")
            self.assertEqual(row["experience_level"], "Fresher / entry-level")
            self.assertEqual(row["posted_at"], "2026-10-01")
            self.assertEqual(row["match_score"], "82")
            self.assertEqual(row["source"], job["source"])

    @patch("job_hunter.requests.get")
    def test_arbeitnow_api_response_is_normalized_without_network(self, mock_get):
        response = Mock()
        response.json.return_value = {
            "data": [{
                "slug": "role-1",
                "title": "Python Developer",
                "company_name": "Example",
                "location": "Hyderabad",
                "description": "Python and SQL. Entry-level.",
                "url": "https://jobs.example/role-1",
                "created_at": "2026-10-01",
                "remote": False,
            }]
        }
        mock_get.return_value = response
        jobs = job_hunter.fetch_arbeitnow()
        mock_get.assert_called_once()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["required_skills"], ["python", "sql"])
        self.assertEqual(jobs[0]["experience_level"], "Fresher / entry-level")
        self.assertEqual(jobs[0]["public_contact_email"], "")

    @patch("job_hunter.requests.get")
    def test_remotive_api_response_is_normalized_without_network(self, mock_get):
        response = Mock()
        response.json.return_value = {
            "jobs": [{
                "id": 12,
                "title": "Junior Django Developer",
                "company_name": "Example",
                "candidate_required_location": "India",
                "description": "Junior role using Django REST Framework.",
                "url": "https://jobs.example/role-12",
                "publication_date": "2026-10-02",
            }]
        }
        mock_get.return_value = response
        jobs = job_hunter.fetch_remotive()
        mock_get.assert_called_once()
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["remote"])
        self.assertIn("django rest framework", jobs[0]["required_skills"])

    @patch("job_hunter.requests.get")
    def test_jobicy_api_follows_cursor_pagination(self, mock_get):
        first_response = Mock()
        first_response.json.return_value = {
            "jobs": [{"id": 1}],
            "nextCursor": "cursor-2",
        }
        second_response = Mock()
        second_response.json.return_value = {
            "jobs": [{"id": 2}],
            "nextCursor": None,
        }
        mock_get.side_effect = [first_response, second_response]

        records = job_hunter.fetch_jobicy_records()

        self.assertEqual([record["id"] for record in records], [1, 2])
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(
            mock_get.call_args_list[0].kwargs["params"],
            {"count": 200},
        )
        self.assertEqual(
            mock_get.call_args_list[1].kwargs["params"],
            {"count": 200, "cursor": "cursor-2"},
        )
        self.assertEqual(mock_get.call_args_list[0].kwargs["timeout"], 25)

    @patch("job_hunter.requests.get")
    def test_jobicy_api_rejects_malformed_payload_and_logs_http_error(self, mock_get):
        malformed = Mock()
        malformed.json.return_value = {"jobs": "not-a-list"}
        mock_get.return_value = malformed
        with patch.object(job_hunter.LOGGER, "warning") as warning:
            self.assertEqual(job_hunter.fetch_jobicy_records(), [])
        warning.assert_called_once()

        throttled = Mock()
        throttled.raise_for_status.side_effect = job_hunter.requests.HTTPError(
            "429 Too Many Requests"
        )
        mock_get.return_value = throttled
        with patch.object(job_hunter.LOGGER, "warning") as warning:
            self.assertEqual(job_hunter.fetch_jobicy_records(), [])
        warning.assert_called_once()
        self.assertEqual(mock_get.call_count, 2)

    @patch("job_hunter.fetch_jobicy_records")
    def test_jobicy_payload_uses_documented_fields_and_keeps_source_url(
        self, fetch_records
    ):
        fetch_records.return_value = [{
            "id": 153012,
            "jobTitle": "AI Intelligence Efficiency Engineer Intern",
            "companyName": "Example Labs",
            "jobGeo": "India",
            "jobLevel": "Entry-Level",
            "jobType": ["Internship"],
            "jobIndustry": ["Software Engineering"],
            "jobDescription": "Build internal tools using Python and APIs.",
            "pubDate": "2026-10-12T02:35:25+00:00",
            "url": "https://jobicy.com/jobs/153012-example-role",
        }]

        jobs = job_hunter.fetch_jobicy()

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "AI Intelligence Efficiency Engineer Intern")
        self.assertEqual(job["company"], "Example Labs")
        self.assertEqual(job["location"], "India")
        self.assertEqual(job["experience"], "Entry-Level")
        self.assertTrue(job["internship"])
        self.assertTrue(job["remote"])
        self.assertIn("python", job["required_skills"])
        self.assertEqual(job["posted_at"], "2026-10-12T02:35:25+00:00")
        self.assertEqual(job["source"], "Jobicy")
        self.assertEqual(job["url"], "https://jobicy.com/jobs/153012-example-role")

    def test_jobicy_apac_only_posting_does_not_bypass_india_eligibility(self):
        job = job_hunter.normalize_job({
            "jobTitle": "AI Application Engineer Intern",
            "companyName": "Example Labs",
            "jobGeo": "APAC",
            "jobLevel": "Entry-Level",
            "jobType": ["Internship"],
            "jobDescription": "Build Python backend services.",
            "url": "https://jobicy.com/jobs/apac-intern",
        }, "Jobicy", remote=True)
        if job is None:
            self.fail("Expected a valid Jobicy listing to be normalized.")

        score, reason = job_hunter.score_job(job)

        self.assertEqual(score, 0)
        self.assertIn("Outside", reason)

    def test_jobicy_explicit_india_internship_uses_shared_matcher(self):
        job = job_hunter.normalize_job({
            "jobTitle": "Junior Python Backend Developer Intern",
            "companyName": "Example Labs",
            "jobGeo": "India",
            "jobLevel": "Entry-Level",
            "jobType": ["Internship"],
            "jobDescription": "Build Python and Django backend services.",
            "url": "https://jobicy.com/jobs/india-intern",
        }, "Jobicy", remote=True)
        if job is None:
            self.fail("Expected a valid Jobicy listing to be normalized.")

        score, reason = job_hunter.score_job(job)

        self.assertGreaterEqual(score, 35)
        self.assertIn("Remote (India-eligible)", reason)

    def test_email_draft_has_no_unconfirmed_degree_or_github_claim(self):
        with patch.dict(
            "os.environ",
            {"CANDIDATE_DEGREE": "", "CANDIDATE_GITHUB_URL": ""},
            clear=False,
        ):
            _, body = job_hunter.make_email_draft(listing())
        self.assertNotIn("B.Tech", body)
        self.assertNotIn("github.com", body)
        self.assertIn("Optimal Guide", body)

    def test_email_draft_uses_configured_degree_and_github(self):
        with patch.dict(
            "os.environ",
            {
                "CANDIDATE_DEGREE": "B.Sc. Computer Science",
                "CANDIDATE_GITHUB_URL": "https://github.com/example",
            },
            clear=False,
        ):
            _, body = job_hunter.make_email_draft(listing())
        self.assertIn("B.Sc. Computer Science", body)
        self.assertIn("https://github.com/example", body)

    @patch("job_hunter.make_email_draft", return_value=("subject", "body"))
    def test_gemini_drafting_uses_local_template_without_api_key(self, template):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            result = job_hunter.gemini_draft(listing())
        self.assertEqual(result, ("subject", "body"))
        template.assert_called_once()


if __name__ == "__main__":
    unittest.main()
