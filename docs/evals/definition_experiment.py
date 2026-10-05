"""Offline evaluation of the frozen title-definition prior; never edits production code.
Run python -B docs/evals/definition_experiment.py. Rubrics were committed before execution.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import knowledge_windows as frozen

HERE = Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / "definition_heldout.json").read_text(encoding="utf-8"))
MIX = json.loads((HERE / "user_mix.json").read_text(encoding="utf-8-sig"))

def read_at(name):
    return subprocess.check_output(["git", "show", FIXTURE["main_revision"]+":"+name], cwd=frozen.ROOT).decode("utf-8")

def measure():
    pinned_knowledge = read_at("src/laptop_agent/knowledge.py")
    current_knowledge = Path(frozen.knowledge.__file__).read_text(encoding="utf-8")
    if current_knowledge != pinned_knowledge:
        raise RuntimeError("This experiment must use knowledge.py at the pinned main revision")
    docs = {name: read_at(name) for name in frozen.DOCS}
    extra = {d["source"]:d["text"] for d in MIX["documents"]}
    subjects = {c["source"]:c["text"] for c in FIXTURE["cases"]}
    result = {"main_revision":FIXTURE["main_revision"], "fixture_sha256":hashlib.sha256((HERE/"definition_heldout.json").read_bytes()).hexdigest(), "runs":[]}
    # Legacy exact-window metrics stay separate from the new semantic judgments.
    corpora = {"README_only":{"README.md":docs["README.md"]},
               "illustrative_user_mix":{"README.md":docs["README.md"], "docs/analytics.md":docs["docs/analytics.md"], **extra},
               "developer_history_stress":docs}
    for panel in ("legacy_windows", "heldout_semantic"):
        panel_corpora = corpora if panel == "legacy_windows" else {
            "single_subject_README":None, "illustrative_user_mix":{**subjects, **extra},
            "developer_history_stress":{**subjects, **docs}}
        for corpus, documents in panel_corpora.items():
            cases = [{"id":str(i),"question":q,"source":gold,"anchors":anchors} for i,(q,gold,anchors) in enumerate(frozen.CASES) if gold in documents] if panel == "legacy_windows" else FIXTURE["cases"]
            for mode in ("baseline", "title_definition"):
                rows = []
                with tempfile.TemporaryDirectory(prefix="jarvis_definition_eval_") as temp:
                    cls = frozen.variant(mode)
                    kb = cls(Path(temp)/"all.json")
                    if documents:
                        for name,text in documents.items():kb.add(name,text)
                    for case in cases:
                        target = kb
                        if documents is None:
                            target=cls(Path(temp)/(case["id"]+".json"));target.add(case["source"],case["text"])
                        one, four = target.answer(case["question"],limit=1), target.answer(case["question"],limit=4)
                        row = {"id":case["id"],"question":case["question"],"one":one,"four":four}
                        if panel == "legacy_windows":
                            def hit(answer):
                                return case["source"] in answer.get("sources",[]) and all(a.casefold() in answer.get("answer","").casefold() for a in case["anchors"])
                            row.update(document_hit=case["source"] in one.get("sources",[]), window_hit=hit(one), up_to_four_hit=hit(four))
                        rows.append(row)
                run = {"panel":panel,"corpus":corpus,"variant":mode,"rows":rows}
                if panel == "legacy_windows":
                    run.update({key:sum(r[key] for r in rows) for key in ("document_hit","window_hit","up_to_four_hit")})
                    print(panel,corpus,mode,len(rows),run["document_hit"],run["window_hit"],run["up_to_four_hit"])
                result["runs"].append(run)
    return result

if __name__ == "__main__":
    result=measure()
    compact={"main_revision":result["main_revision"],"fixture_sha256":result["fixture_sha256"],"runs":[]}
    for run in result["runs"]:
        saved={key:value for key,value in run.items() if key!="rows"}
        if run["panel"]=="legacy_windows":
            saved["rows"]=[{key:row[key] for key in ("id","document_hit","window_hit","up_to_four_hit")} for row in run["rows"]]
        else:
            saved["rows"]=[{"id":row["id"],"source":row["one"].get("sources",[]),"answer":row["one"].get("answer","")} for row in run["rows"]]
        compact["runs"].append(saved)
    (HERE/"definition_results.json").write_text(json.dumps(compact,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for run in result["runs"]:
        if run["panel"]=="heldout_semantic" and run["corpus"]=="single_subject_README":
            for row in run["rows"]:print(run["variant"],row["id"],ascii(row["one"].get("answer","")))
