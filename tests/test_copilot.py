from __future__ import annotations

import unittest

from laptop_agent.copilot import JobCopilot, ats_score, check_grounding, extract_keywords, extract_resume_claims

RESUME = (
    "Jeevan Arlagadda\n"
    "- Built a Python FastAPI service handling 2M requests/day with Redis caching.\n"
    "- Led migration to Kubernetes, cutting deploy time 40%.\n"
    "- Mentored 3 engineers on testing and code review.\n"
)
JD = "We need a backend engineer strong in Python, FastAPI, Redis, and Kubernetes. AWS is a plus."


class PortedLogicTests(unittest.TestCase):
    def test_extract_keywords_dedup_and_stopwords(self) -> None:
        kws = extract_keywords(JD)
        self.assertIn("python", kws)
        self.assertIn("kubernetes", kws)
        self.assertNotIn("and", kws)  # stopword dropped
        self.assertEqual(len(kws), len(set(kws)))  # deduped

    def test_ats_score_hits_and_misses(self) -> None:
        score = ats_score(extract_keywords(JD), RESUME)
        self.assertGreater(score["score"], 0)
        self.assertIn("aws", [m.lower() for m in score["misses"]])  # AWS not in resume
        self.assertTrue(any(h.lower() == "python" for h in score["hits"]))

    def test_resume_claims_and_grounding(self) -> None:
        claims = extract_resume_claims(RESUME)
        self.assertEqual(len(claims), 3)  # three bullet lines
        grounded = check_grounding(["- Built a Python FastAPI service with Redis"], claims)
        self.assertEqual(grounded["flagged"], [])  # overlaps the resume
        invented = check_grounding(["- Won a Nobel Prize in astrophysics"], claims)
        self.assertEqual(len(invented["flagged"]), 1)  # no overlap -> flagged


class TailorTests(unittest.TestCase):
    def test_tailor_without_llm_gives_score_and_missing(self) -> None:
        result = JobCopilot().tailor(RESUME, JD, company="Acme", role="Backend Engineer")
        self.assertTrue(result.ok)
        self.assertFalse(result.used_llm)
        self.assertIn("ATS match", result.package)
        self.assertIn("aws", [m.lower() for m in result.missing])

    def test_tailor_with_llm_includes_package_and_grounding(self) -> None:
        seen = {}

        def decide(prompt: str) -> str:
            seen["prompt"] = prompt
            return "## Tailored resume bullets\n- Built a Python FastAPI service with Redis and Kubernetes."

        result = JobCopilot(decide=decide).tailor(RESUME, JD)
        self.assertTrue(result.used_llm)
        self.assertIn("FastAPI", result.package)
        self.assertIn("RESUME:", seen["prompt"])  # resume handed to the model
        self.assertEqual(result.grounding["flagged"], [])  # the bullet overlaps the resume

    def test_tailor_requires_both_inputs(self) -> None:
        self.assertFalse(JobCopilot().tailor("", JD).ok)
        self.assertFalse(JobCopilot().tailor(RESUME, "").ok)

    def test_llm_error_falls_back(self) -> None:
        def boom(_p):
            raise RuntimeError("model down")

        result = JobCopilot(decide=boom).tailor(RESUME, JD)
        self.assertTrue(result.ok)  # still returns the ATS score
        self.assertFalse(result.used_llm)
        self.assertIn("ATS match", result.package)


if __name__ == "__main__":
    unittest.main()
