"""Measure local LLM request understanding on a fixed multilingual text corpus.

This is text/plan accuracy, not microphone recognition or real-world accuracy.
No call is originated; every evaluated input goes through the production session.
"""
import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from time import monotonic
from datetime import datetime, timezone
from statistics import median

from app.config import ROOT, settings
from app.conversation import CallSession
from app.record_qa import answer_request
from app.language import subject_label
from app.csv_store import find_by_student_id


def main(limit=None, ids=None):
    settings.llm_understand_every_input = True
    settings.ollama_enabled = True
    student = find_by_student_id('demo-001')
    path = ROOT / 'tests/fixtures/record_qa_questions.json'
    if not path.exists():
        path = Path('/tmp/qa-validation/tests/fixtures/record_qa_questions.json')
    raw = path.read_bytes()
    questions = json.loads(raw)
    selected = [(index, case) for index, case in enumerate(questions, 1)
                if not ids or index in ids]
    if limit:
        selected = selected[:limit]
    results = []
    directory = ROOT / 'runtime/deployment/local-sip/qa-evaluation'
    directory.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Fixed text question-understanding corpus; not live speech or independent real-world accuracy',
              'corpus_sha256': hashlib.sha256(raw).hexdigest(), 'model': settings.ollama_model,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'code_sha256': hashlib.sha256((ROOT / 'app/record_qa.py').read_bytes()).hexdigest(),
              'record_id': student.student_id,
              'results': results}
    started = monotonic()
    for index, case in selected:
        session = CallSession(expected_phone=student.parent_phone, records=[student])
        reference = CallSession(expected_phone=student.parent_phone, records=[student])
        reference.greeting()
        reference.handle_text({'en': '1', 'hi': '2', 'kn': '3'}[case['language']], use_llm=False)
        reference.handle_text(student.parent_name, use_llm=False)
        session.greeting()
        session.handle_text({'en': '1', 'hi': '2', 'kn': '3'}[case['language']], use_llm=False)
        assert session.handle_text(student.parent_name, use_llm=False).verified
        for text in case.get('prelude', []):
            session.handle_text(text, use_llm=True)
            reference.handle_text(text, use_llm=False)
        previous_subjects = list(session.last_subjects)
        expected_answers = []
        for i, kind in enumerate(case['kinds']):
            if kind in {'goodbye', 'opt_out'}:
                continue
            expected_request = dict(kind=kind,
                subjects=case.get('subjects', [[] for _ in case['kinds']])[i],
                value=case.get('values', [None for _ in case['kinds']])[i],
                unit=case.get('units', ['percent' if item == 'percentage' else 'marks'
                                      for item in case['kinds']])[i], inclusive=False)
            expected_answers.append(answer_request(student, expected_request, case['language'],
                                    reference.last_answer_reply, previous_subjects))
        expected_text = ' '.join(expected_answers)
        turn = session.handle_text(case['question'], use_llm=True)
        requests = session.last_requests
        actual = [request['kind'] for request in requests]
        passed = actual == case['kinds']
        if passed and 'subjects' in case:
            passed = all(set(requests[i]['subjects']) == set(subjects)
                         for i, subjects in enumerate(case['subjects']))
        if passed and 'values' in case:
            passed = all(requests[i]['value'] == value for i, value in enumerate(case['values']))
        if passed and 'units' in case:
            passed = all(requests[i]['unit'] == value for i, value in enumerate(case['units']))
        # Each understood answer must still come from the authorized record.
        if not turn.verified and case['kinds'] != ['opt_out']:
            passed = False
        # A correct kind alone is insufficient: check quantities and subject scope.
        # The fact renderer has independent arithmetic/record regression tests.
        expected_numbers = Counter(re.findall(r'\d+(?:\.\d+)?', expected_text))
        actual_numbers = Counter(re.findall(r'\d+(?:\.\d+)?', turn.reply))
        facts_correct = expected_numbers == actual_numbers
        if case['kinds'] not in [['goodbye'], ['opt_out']]:
            facts_correct = facts_correct and all(
                expected_text.count(subject_label(case['language'], key)) ==
                turn.reply.count(subject_label(case['language'], key)) for key in student.marks)
        passed = passed and facts_correct
        result = {'id': index, 'language': case['language'], 'question': case['question'],
                  'expected': case, 'requests': requests, 'reply': turn.reply,
                  'interpretation': session.last_interpretation,
                  'facts_correct': facts_correct, 'expected_facts_text': expected_text,
                  'passed': passed, 'llm_used': session.last_llm_used,
                  'seconds': session.last_llm_seconds}
        results.append(result)
        if not passed:
            print(json.dumps({'failed': index, 'language': case['language'], 'question': case['question'],
                              'expected': case['kinds'], 'actual': requests}, ensure_ascii=False), flush=True)
        report.update(tested=len(results), passed=sum(item['passed'] for item in results),
                      accuracy_percent=round(100 * sum(item['passed'] for item in results) / len(results), 2),
                      median_llm_seconds=round(median(item['seconds'] for item in results), 3),
                      p95_llm_seconds=round(sorted(item['seconds'] for item in results)[int((len(results)-1)*.95)], 3),
                      seconds=round(monotonic() - started, 2))
        (directory / 'latest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if len(results) % 10 == 0:
            print(f"Progress {len(results)}/{len(selected)}: {report['accuracy_percent']}%", flush=True)
    print(json.dumps({key: value for key, value in report.items() if key != 'results'}, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--ids', help='Comma-separated case ids for investigating failures')
    args = parser.parse_args()
    main(args.limit, {int(value) for value in args.ids.split(',')} if args.ids else None)
