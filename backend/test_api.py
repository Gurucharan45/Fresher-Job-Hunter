import csv
import tempfile
import unittest

from fastapi.testclient import TestClient

import job_hunter
from backend.main import app


class JobHunterApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        job_hunter.init_db()

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_jobs_list_and_update_status(self):
        job = {
            "title": "Junior Python Developer",
            "company": "Example Co",
            "location": "Hyderabad, Telangana",
            "description": "Fresher role using Python and Django.",
            "url": "https://jobs.example/api/list/123",
            "posted_at": "2026-10-01",
            "required_skills": ["python", "django", "sql"],
            "experience_level": "Fresher / entry-level",
            "source": "Example",
            "remote": False,
            "match_score": 88,
            "match_reason": "role: python developer; skills: python, django",
        }
        job_key = job_hunter.job_key(job)
        job_hunter.store_job(job, 88, job["match_reason"], "Subject", "Draft body")

        list_response = self.client.get("/api/jobs")
        self.assertEqual(list_response.status_code, 200)
        self.assertGreaterEqual(list_response.json()["total"], 1)

        update_response = self.client.patch(
            f"/api/jobs/{job_key}",
            json={"status": "contacted"},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["status"], "contacted")

        detail_response = self.client.get(f"/api/jobs/{job_key}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["status"], "contacted")

    def test_csv_import_preview_endpoint(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", delete=False, encoding="utf-8") as source:
            writer = csv.writer(source)
            writer.writerow([
                "Job Title",
                "Company",
                "Location",
                "Description",
                "Experience",
                "Job Type",
                "Posting Date",
                "Source",
                "Original Application URL",
                "Remote",
                "Tags",
            ])
            writer.writerow([
                "Junior Python Developer",
                "Example Co",
                "Hyderabad, Telangana",
                "Fresher role using Python and Django.",
                "0-1 year",
                "Full-time",
                "2026-10-01",
                "Permitted board",
                "https://jobs.example/import/1",
                "no",
                "Python; Django; SQL",
            ])
            path = source.name

        try:
            with open(path, "rb") as handle:
                response = self.client.post(
                    "/api/jobs/import-csv",
                    files={"file": ("jobs.csv", handle, "text/csv")},
                    data={"save": "false"},
                )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["saved"], False)
            self.assertEqual(payload["report"]["rows_read"], 1)
            self.assertEqual(len(payload["report"]["accepted"]), 1)
        finally:
            import os
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
