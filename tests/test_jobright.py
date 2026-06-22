from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.jobs import JobTracker
from laptop_agent.tools.jobright import (
    clean_title,
    deduplicate,
    extract_jobs_from_api_data,
    is_relevant,
    parse_jobright_item,
    try_parse_job_object,
)

# A trimmed sample of Jobright's recommendation API payload shape.
SAMPLE_API = {
    "result": {
        "jobList": [
            {
                "jobResult": {
                    "jobId": "abc123",
                    "jobTitle": "Senior Software Engineer, Backend",
                    "jobLocation": "Austin, TX",
                    "isRemote": True,
                    "originalUrl": "https://careers.example.com/abc123",
                    "jobSummary": "Build backend services.",
                    "coreResponsibilities": ["Design APIs", "Own services"],
                    "publishTimeDesc": "2 days ago",
                },
                "companyResult": {"companyName": "Acme", "companyLocation": "Austin, TX"},
            },
            {
                "jobResult": {
                    "jobId": "def456",
                    "jobTitle": "Director of Engineering",
                    "jobLocation": "Remote",
                },
                "companyResult": {"companyName": "Globex"},
            },
        ]
    }
}


class JobrightParsingTests(unittest.TestCase):
    def test_parse_jobright_item_builds_normalized_lead(self) -> None:
        item = SAMPLE_API["result"]["jobList"][0]
        lead = parse_jobright_item(item)
        assert lead is not None
        self.assertEqual(lead["title"], "Senior Software Engineer, Backend")
        self.assertEqual(lead["company"], "Acme")
        self.assertEqual(lead["job_id_ext"], "abc123")
        self.assertEqual(lead["job_url"], "https://careers.example.com/abc123")
        self.assertIn("(Remote)", lead["location"])
        self.assertIn("Design APIs", lead["description"])

    def test_parse_jobright_item_rejects_malformed(self) -> None:
        self.assertIsNone(parse_jobright_item({"jobResult": {}, "companyResult": {}}))

    def test_extract_walks_nested_structure(self) -> None:
        jobs = extract_jobs_from_api_data(SAMPLE_API)
        titles = {j["title"] for j in jobs}
        self.assertIn("Senior Software Engineer, Backend", titles)
        self.assertIn("Director of Engineering", titles)

    def test_try_parse_generic_job_object(self) -> None:
        lead = try_parse_job_object(
            {"title": "Data Engineer", "company": {"name": "Initech"}, "id": "x1", "location": "NYC"}
        )
        assert lead is not None
        self.assertEqual(lead["company"], "Initech")
        self.assertTrue(lead["job_url"].endswith("/jobs/x1"))

    def test_is_relevant_filters_titles(self) -> None:
        self.assertTrue(is_relevant("Backend Software Engineer"))
        self.assertFalse(is_relevant("Director of Engineering"))  # excluded
        self.assertFalse(is_relevant("Barista"))  # no target keyword

    def test_clean_title_strips_relative_time(self) -> None:
        self.assertEqual(clean_title("5 minutes ago Senior Software Engineer"), "Senior Software Engineer")
        self.assertEqual(clean_title("just now — Data Engineer"), "Data Engineer")
        self.assertEqual(clean_title("Posted 3 days ago Backend Developer"), "Backend Developer")
        self.assertEqual(clean_title("Software Engineer"), "Software Engineer")  # untouched
        self.assertEqual(clean_title("Airtable • 5 minutes ago"), "Airtable")  # trailing time in company

    def test_deduplicate_by_title_company(self) -> None:
        rows = [
            {"title": "SWE", "company": "Acme"},
            {"title": "swe", "company": "acme"},
            {"title": "SWE", "company": "Globex"},
        ]
        self.assertEqual(len(deduplicate(rows)), 2)


class ImportLeadsTests(unittest.TestCase):
    def _tracker(self, root: str) -> JobTracker:
        return JobTracker(Path(root) / "jobs.json")

    def test_import_adds_leads_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jt = self._tracker(raw)
            leads = [
                {"title": "ML Engineer", "company": "Acme", "job_id_ext": "a1", "job_url": "u1", "description": "jd"},
                {"title": "Data Scientist", "company": "Globex", "job_id_ext": "g1"},
            ]
            summary = jt.import_leads(leads)
            self.assertEqual(summary["added"], 2)
            self.assertEqual(summary["skipped"], 0)
            stored = jt.list()
            self.assertTrue(all(j["stage"] == "lead" for j in stored))
            self.assertTrue(all(j["source"] == "jobright" for j in stored))

            # Second pull: same external id is skipped.
            again = jt.import_leads(leads)
            self.assertEqual(again["added"], 0)
            self.assertEqual(again["skipped"], 2)

    def test_import_skips_when_company_or_title_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jt = self._tracker(raw)
            summary = jt.import_leads([{"title": "", "company": "Acme"}, {"title": "SWE", "company": ""}])
            self.assertEqual(summary["added"], 0)
            self.assertEqual(summary["skipped"], 2)

    def test_leads_excluded_from_response_rate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jt = self._tracker(raw)
            jt.import_leads([{"title": "SWE", "company": "Acme", "job_id_ext": "a1"}])
            jt.add("Globex", stage="applied")
            jt.add("Initech", stage="interview")
            stats = jt.stats()
            self.assertEqual(stats["leads"], 1)
            self.assertEqual(stats["total"], 3)
            # response rate is over the 2 applications, not the 3 total.
            self.assertEqual(stats["response_rate"], round(1 / 2, 3))


if __name__ == "__main__":
    unittest.main()
