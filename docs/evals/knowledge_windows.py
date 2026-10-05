"""Offline, fixed-window evaluation; never changes the production ranking module.

Run: python -B docs/evals/knowledge_windows.py
"""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import laptop_agent.knowledge as knowledge

# Frozen before the first run. These locate specific known-answer windows, not all
# semantically acceptable answers. A miss may still contain a correct paraphrase.
CASES = [
    ('what is jarvis', 'README.md', ['local-first, voice-capable']),
    ('what is J.A.R.V.I.S', 'README.md', ['local-first, voice-capable']),
    ('how do I start the web app', 'README.md', ['python -m laptop_agent.webui']),
    ('which model handles vision', 'README.md', ['meta/llama-3.2-11b-vision-instruct']),
    ('which model generates pictures', 'README.md', ['black-forest-labs/flux.2-klein-4b']),
    ('how do I run browser tests', 'README.md', ['JARVIS_BROWSER_TESTS']),
    ('how do I enable LAN mode', 'README.md', ['LAPTOP_AGENT_HOST', 'LAPTOP_AGENT_LAN_PASSCODE']),
    ('how do I sign in with Google', 'README.md', ['GOOGLE_CLIENT_ID']),
    ('what happens when approval times out', 'README.md', ['No answer means no']),
    ('how do I record a voice note', 'README.md', ['record voice upto 20 seconds']),
    ('how does barge-in work', 'README.md', ['three or more words']),
    ('how are embeddings used for recall', 'README.md', ['Asymmetric: documents embed']),
    ('how is out of sample R2 computed', 'docs/analytics.md', ['sum((actual - train_mean)^2)']),
    ('what happens when MAD is zero', 'docs/analytics.md', ['unscored_indices']),
    ('when are VIF warnings shown', 'docs/analytics.md', ['VIF >= 10']),
]
DOCS = ['README.md', 'CLAUDE.md', 'MEMORY.md', 'docs/analytics.md']
TEXTS = {name: (ROOT / name).read_text(encoding='utf-8') for name in DOCS}
for _, name, anchors in CASES:
    assert all(anchor in TEXTS[name] for anchor in anchors), (name, anchors)


def variant(mode):
    if mode == 'baseline':
        return knowledge.KnowledgeBase
    # An in-memory experimental copy, not a source edit. Retain the document rank,
    # passage candidates, query terms and all selection rules. Only vary the prior.
    source = Path(knowledge.__file__).read_text(encoding='utf-8')
    marker = '                score *= _prose_weight(passage)'
    assert source.count(marker) == 1
    if mode == 'position':
        bonus = '1 + 2 / (1 + start / 3)'
    elif mode == 'title_definition':
        # Question-level definition intent and overlap with the document title.
        bonus = "(3 if start == 0 and question.lower().startswith('what is ') and (terms - _FUNCTION_WORDS).intersection(_content_terms(sentences[0])) else 1)"
    else:
        raise ValueError(mode)
    module = types.ModuleType('eval_knowledge_' + mode)
    exec(compile(source.replace(marker, marker + '\n                score *= ' + bonus), knowledge.__file__, 'exec'), module.__dict__)
    return module.KnowledgeBase


def measure():
    result = {'corpus_sha256': {n: hashlib.sha256(t.encode()).hexdigest() for n,t in TEXTS.items()}, 'runs': []}
    for corpus in ('single_gold_document', 'four_documents'):
        for mode in ('baseline', 'position', 'title_definition'):
            with tempfile.TemporaryDirectory(prefix='jarvis_knowledge_eval_') as temp:
                cls = variant(mode)
                kb = cls(Path(temp) / 'all.json')
                for name in DOCS:
                    kb.add(name, TEXTS[name])
                rows = []
                for index, (question, gold, anchors) in enumerate(CASES):
                    target = kb
                    if corpus == 'single_gold_document':
                        target = cls(Path(temp) / f'one{index}.json')
                        target.add(gold, TEXTS[gold])
                    answer = target.answer(question, limit=1)
                    multi = target.answer(question)
                    excerpt = answer.get('excerpts', [{}])[0]
                    def hits(value):
                        return gold in value.get('sources', []) and all(a.casefold() in value.get('answer', '').casefold() for a in anchors)
                    rows.append({'question': question, 'gold': gold, 'anchors': anchors,
                                 'document_hit': gold in answer.get('sources', []),
                                 'window_hit': hits(answer), 'up_to_four_hit': hits(multi),
                                 'score': excerpt.get('score'), 'source': excerpt.get('source'),
                                 'answer': answer.get('answer', '')})
                result['runs'].append({'corpus': corpus, 'variant': mode, 'rows': rows,
                    **{key: sum(row[key] for row in rows) for key in ('document_hit', 'window_hit', 'up_to_four_hit')}})
    return result


if __name__ == '__main__':
    result = measure()
    output = Path(__file__).with_name('knowledge_windows_results.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for run in result['runs']:
        print(run['corpus'], run['variant'], {k:run[k] for k in ('document_hit','window_hit','up_to_four_hit')})
    for run in result['runs']:
        if run['variant'] == 'baseline':
            print(run['corpus'])
            for row in run['rows']:
                print(row['window_hit'], row['question'], row['source'], ascii(row['answer'][:100]))
