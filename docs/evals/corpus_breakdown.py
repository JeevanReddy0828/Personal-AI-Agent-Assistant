"""Compare corpora separately with the unchanged production scorer and frozen questions.

Run from this repository: python -B docs/evals/corpus_breakdown.py
Git objects 0ce6847 (before note move) and 6ebd8e0 (after) must be available locally.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import knowledge_windows as frozen

HERE = Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / 'user_mix.json').read_text(encoding='utf-8-sig'))


def read_at(commit, name):
    return subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=frozen.ROOT).decode('utf-8')


def main():
    result = {'fixture': FIXTURE['description'], 'fixture_sha256': hashlib.sha256((HERE/'user_mix.json').read_bytes()).hexdigest(), 'runs': []}
    for commit in ('0ce6847', '6ebd8e0'):
        docs = {name: read_at(commit, name) for name in frozen.DOCS}
        corpora = {
            'README_only': {'README.md': docs['README.md']},
            'illustrative_user_mix': {'README.md': docs['README.md'], 'docs/analytics.md': docs['docs/analytics.md'],
                                      **{d['source']: d['text'] for d in FIXTURE['documents']}},
            'developer_history_stress': docs,
        }
        for corpus, documents in corpora.items():
            cases = [case for case in frozen.CASES if case[1] in documents]
            with tempfile.TemporaryDirectory(prefix='jarvis_corpus_eval_') as temporary:
                kb = frozen.knowledge.KnowledgeBase(Path(temporary)/'knowledge.json')
                for name, text in documents.items():
                    kb.add(name, text)
                rows = []
                for question, gold, anchors in cases:
                    one, four = kb.answer(question, limit=1), kb.answer(question)
                    def hit(answer):
                        return gold in answer.get('sources', []) and all(a.casefold() in answer.get('answer', '').casefold() for a in anchors)
                    rows.append({'question': question, 'gold': gold, 'anchors': anchors,
                        'document_hit': gold in one.get('sources', []), 'window_hit': hit(one),
                        'up_to_four_hit': hit(four), 'source': one.get('sources', []),
                        'answer': one.get('answer', ''), 'score': one.get('excerpts', [{}])[0].get('score')})
                run = {'commit': commit, 'corpus': corpus, 'questions': len(rows),
                       'documents': {name: {'chars':len(text), 'sha256':hashlib.sha256(text.encode()).hexdigest()} for name,text in documents.items()},
                       'rows': rows, **{key:sum(row[key] for row in rows) for key in ('document_hit','window_hit','up_to_four_hit')}}
                result['runs'].append(run)
                print(commit, corpus, len(rows), run['document_hit'], run['window_hit'], run['up_to_four_hit'], flush=True)
    (HERE/'corpus_breakdown_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
