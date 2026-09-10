from __future__ import annotations

import json
import unittest

from laptop_agent.copilot import (
    JobCopilot,
    ats_score,
    check_grounding,
    check_resume_grounding,
    extract_keywords,
    extract_resume_claims,
    render_resume_html,
)

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

    def test_ats_scores_symbol_languages(self) -> None:
        # C++/C# end in non-word chars; the naive \b...\b boundary missed them (scored 33%).
        score = ats_score(["c++", "c#", "python"], "Languages: C++, C#, Python and SQL")
        self.assertEqual(score["score"], 100)
        self.assertEqual(score["misses"], [])

    def test_ats_respects_token_boundaries(self) -> None:
        score = ats_score(["java", "c"], "Experienced with JavaScript and objective-cobalt")
        self.assertIn("java", [m.lower() for m in score["misses"]])  # not inside "JavaScript"
        self.assertIn("c", [m.lower() for m in score["misses"]])

    def test_extract_keywords_keeps_symbol_skills(self) -> None:
        kws = extract_keywords("Strong in C# and C++ and Go")
        self.assertIn("c#", kws)
        self.assertIn("c++", kws)

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


class TailorResumeTests(unittest.TestCase):
    JSON = json.dumps({
        "summary": "Backend engineer with Python and Kubernetes experience.",
        "skills": [{"category": "Languages", "items": "Python, SQL"}],
        "experiences": [{"company": "Acme", "dates": "2023", "title": "Engineer",
                         "location": "NYC", "bullets": ["Built a Python FastAPI service with Redis"]}],
        "projects": [{"name": "Proj", "stack": "Python", "repo_url": "https://github.com/u/proj", "bullet": "Did X"}],
        "education": [{"school": "UF", "degree": "M.S. CS", "dates": "2024"}],
    })

    SOURCE = RESUME + "\nBackend engineer with Python and Kubernetes experience.\nLanguages: Python, SQL\nAcme 2023 Engineer NYC\nBuilt a Python FastAPI service with Redis\nProj Python Did X https://github.com/u/proj\nUF M.S. CS 2024\n"

    def test_resume_requires_llm(self) -> None:
        result = JobCopilot().tailor_resume(RESUME, JD, company="Acme", role="Backend")
        self.assertFalse(result.ok)
        self.assertIn("language model", result.package.lower())

    def test_resume_renders_template_from_json(self) -> None:
        seen = {}

        def decide(prompt: str) -> str:
            seen["prompt"] = prompt
            return self.JSON

        repos = [{"name": "proj", "url": "https://github.com/u/proj"}]
        result = JobCopilot(decide=decide).tailor_resume(
            self.SOURCE, JD, company="Acme", role="Backend", repos=repos,
            contact="<a href='x'>LinkedIn</a>", certs="<a href='y'>AWS</a>", name="JEEVAN")
        self.assertTrue(result.ok)
        self.assertIn("<!DOCTYPE", result.package)
        self.assertIn("JEEVAN", result.package)  # name from caller
        self.assertIn("LinkedIn", result.package)  # contact injected verbatim
        self.assertIn("AWS", result.package)  # certs injected verbatim
        self.assertIn("https://github.com/u/proj", result.package)  # grounded repo link
        self.assertIn("proj: https://github.com/u/proj", seen["prompt"])  # repo handed to model

    def test_resume_rejects_invalid_content(self) -> None:
        self.assertFalse(JobCopilot(decide=lambda p: "Sorry, no JSON here.").tailor_resume(RESUME, JD).ok)
        self.assertFalse(JobCopilot(decide=lambda p: '{"summary":"x"}').tailor_resume(RESUME, JD).ok)  # no experiences

    def test_resume_parses_fenced_json(self) -> None:
        result = JobCopilot(decide=lambda p: "```json\n" + self.JSON + "\n```").tailor_resume(self.SOURCE, JD)
        self.assertTrue(result.ok)
        self.assertIn("Technical Skills".upper(), result.package.upper())

    def test_render_survives_malformed_shapes(self) -> None:
        # Models sometimes emit strings/nulls where the schema wants objects; render must
        # skip them, not raise AttributeError.
        html = render_resume_html("N", "", "", {
            "experiences": ["bad shape", {"company": "Acme", "bullets": "not a list"}],
            "skills": "nope",
            "projects": [None, 5],
            "education": [123],
        })
        self.assertIn("<!DOCTYPE", html)
        self.assertIn("Acme", html)

    def test_tailor_resume_rejects_non_object_experiences(self) -> None:
        result = JobCopilot(decide=lambda p: '{"experiences":["bad shape"]}').tailor_resume(RESUME, JD)
        self.assertFalse(result.ok)  # no usable experience objects

    def test_grounding_flags_invented_entities(self) -> None:
        data = {
            "summary": "Backend engineer who boosted revenue 87%.",
            "experiences": [{"company": "MegaCorp", "title": "Staff", "dates": "2019",
                             "bullets": ["Led a team at MegaCorp"]}],
            "education": [{"school": "Fake University", "degree": "PhD", "dates": "2018"}],
        }
        flags = check_resume_grounding(data, RESUME)
        self.assertTrue(any("MegaCorp" in f for f in flags))       # invented employer
        self.assertTrue(any("Fake University" in f for f in flags))  # invented school
        self.assertTrue(any("87%" in f for f in flags))            # invented metric

    def test_grounding_passes_traceable_content(self) -> None:
        data = {"summary": "Led migration to Kubernetes, cutting deploy time 40%.",
                "experiences": [{"company": "", "bullets": ["Mentored 3 engineers"]}],
                "education": []}
        self.assertEqual(check_resume_grounding(data, RESUME), [])  # 40 and 3 are in RESUME


if __name__ == "__main__":
    unittest.main()
