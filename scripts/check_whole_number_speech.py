"""No-call check of actual native voice output with whole-number pronunciation."""
import json
from pathlib import Path
import shutil
import wave

from app.audio_cache import cached_speech
from app.config import ROOT
from app.conversation import CallSession
from app.csv_store import find_by_student_id
from scripts.vits_service import normalize


def main():
    student = find_by_student_id('demo-001')
    if student is None:
        raise RuntimeError('Demo record missing')
    directory = ROOT / 'runtime/deployment/local-sip/whole-number-audio'
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    expected = {'en': ('three hundred and forty-two', 'four hundred', 'eighty-five point five'),
                'hi': ('तीन सौ बयालीस', 'चार सौ', 'पचासी दशमलव पाँच'),
                'kn': ('ಮುನ್ನೂರ ನಲವತ್ತೆರಡು', 'ನಾನೂರ', 'ಎಂಭತ್ತೈದು ದಶಮಾಂಶ ಐದು')}
    # These expectations describe the supplied 342/400, 85.5% demo dataset.
    if student.total != 342 or student.max_total != 400 or student.percentage != 85.5:
        raise RuntimeError('Update the reference expectations for the changed demo marks')
    for language, digit in [('en', '1'), ('hi', '2'), ('kn', '3')]:
        session = CallSession(expected_phone=student.parent_phone, records=[student])
        session.greeting()
        session.handle_text(digit, use_llm=False)
        assert session.handle_text(student.parent_name, use_llm=False).verified
        for kind, query in [('total', 'What is the total?'), ('percentage', 'What is the percentage?')]:
            reply = session.handle_text(query, use_llm=False).reply
            spoken = normalize(reply, language)
            required = expected[language][:2] if kind == 'total' else expected[language][2:]
            assert all(word in spoken for word in required), spoken
            audio = cached_speech(reply, language)
            target = directory / f'{language}-{kind}.wav'
            shutil.copy2(audio, target)
            with wave.open(str(target)) as wav:
                assert wav.getnframes() > 0 and wav.getframerate() == 8000
                seconds = wav.getnframes() / wav.getframerate()
            result.append({'language': language, 'kind': kind, 'reply': reply,
                           'spoken_text': spoken, 'audio': str(target), 'seconds': seconds})
    (directory / 'check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
