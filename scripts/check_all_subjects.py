"""No-call check of every CSV subject in English/Hindi/Kannada answers and audio."""
import json
from pathlib import Path

from app.audio_cache import cached_speech
from app.conversation import CallSession
from app.csv_store import find_by_student_id
from app.language import phrase, subject_label


def main():
    student = find_by_student_id('demo-001')
    if student is None:
        raise RuntimeError('Fictional demo record missing')
    results = []
    cases = [('en', 'Tell me all subjects marks'),
             ('hi', 'सारे सब्जेक्ट के मार्क बताइए'),
             ('kn', 'ಎಲ್ಲಾ ವಿಷಯಗಳ ಅಂಕಗಳನ್ನು ಹೇಳಿ'),
             ('hi', 'सेपरट सब्जेक्ट् के मार्कस बताइए'),
             ('hi', 'सारे अंक बोल दीजिये')]
    for language, question in cases:
        session = CallSession(expected_phone=student.parent_phone, records=[student])
        session.greeting()
        session.handle_text({'en':'1', 'hi':'2', 'kn':'3'}[language], use_llm=False)
        confirmation = session.handle_text(student.parent_name, use_llm=False)
        if not confirmation.verified:
            raise AssertionError('Demo identity confirmation failed')
        result = session.handle_text(question, use_llm=True)
        for name, score in student.marks.items():
            expected = phrase(language, 'mark', subject=subject_label(language, name),
                              score=score, maximum=100)
            if expected not in result.reply:
                raise AssertionError(f'All-subject request {question!r} omitted {name}: {result.reply}')
        path = cached_speech(result.reply, language)
        results.append({'language':language, 'question':question, 'reply':result.reply,
                        'subject_count':len(student.marks), 'audio':str(path)})
    target = Path(__file__).resolve().parents[1] / 'runtime/deployment/local-sip/all-subjects-check.json'
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
