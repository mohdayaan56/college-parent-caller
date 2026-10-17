"""Prepare synthetic parent speech and exact expected replies; never dial."""
import json
from pathlib import Path
import shutil
import wave

from app.audio_cache import cached_speech
from app.conversation import CallSession, with_followup
from app.record_qa import answer_request
from app.language import phrase
from app.csv_store import find_by_student_id
from app.config import ROOT


def main():
    student = find_by_student_id('demo-001')
    if student is None:
        raise RuntimeError('Demo record missing')
    directory = ROOT / 'runtime/deployment/local-sip/audio-test'
    directory.mkdir(parents=True, exist_ok=True)
    scripts = {
        'en': ["Yes, you are speaking with me.", 'Please tell me the marks of all the subjects.',
               'What is his score in Mathematics?', 'His total marks, can you tell me?',
               'What is his overall percentage?', 'Goodbye.'],
        'hi': ['जी हाँ, आप मुझसे ही बात कर रहे हैं।', 'सभी विषयों के अंक बताइए।',
               'कुल अंक कितने हैं?', 'प्रतिशत कितना है?', 'बस इतना ही, धन्यवाद।'],
        'kn': ['ಹೌದು, ನಾನೇ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ.', 'ಎಲ್ಲಾ ವಿಷಯಗಳ ಅಂಕಗಳನ್ನು ಹೇಳಿ.',
               'ಒಟ್ಟು ಅಂಕಗಳು ಎಷ್ಟು?', 'ಶೇಕಡಾವಾರು ಎಷ್ಟು?', 'ಇಷ್ಟು ಸಾಕು, ಧನ್ಯವಾದಗಳು.'],
    }
    stages = {language: ['identity', 'all_subjects', 'total', 'percentage', 'goodbye'] for language in scripts}
    stages['en'].insert(2, 'mathematics')
    result = {'student_name': student.student_name, 'parent_name': student.parent_name,
              'marks': student.marks, 'total': student.total, 'maximum': student.max_total,
              'percentage': student.percentage, 'languages': {}}
    for language, texts in scripts.items():
        session = CallSession(expected_phone=student.parent_phone, records=[student])
        session.greeting()
        prompt = session.handle_text({'en': '1', 'hi': '2', 'kn': '3'}[language], use_llm=False)
        prompt_audio = cached_speech(prompt.reply, language)
        steps = []
        for index, text in enumerate(texts):
            source = cached_speech(text, language)
            target = directory / f'{language}-parent-{index}.wav'
            shutil.copyfile(source, target)
            # The first expected answer follows verified identity. The actual
            # test must still recognize and verify the spoken input itself.
            stage = stages[language][index]
            if stage == 'identity':
                response = session.handle_text(student.parent_name, use_llm=False)
                if not response.verified:
                    raise AssertionError('Reference identity did not verify')
                expected_reply = response.reply
            elif stage == 'goodbye':
                expected_reply = phrase(language, 'bye', parent=student.parent_name)
            else:
                request = dict(kind='subject' if stage == 'mathematics' else stage,
                               subjects=['maths'] if stage == 'mathematics' else [],
                               value=None, unit='percent' if stage == 'percentage' else 'marks', inclusive=False)
                expected_reply = with_followup(answer_request(student, request, language), language)
            expected = cached_speech(expected_reply, language)
            with wave.open(str(target), 'rb') as wav:
                seconds = wav.getnframes() / wav.getframerate()
            steps.append({'stage': stage,
                          'spoken_text': text, 'input_wav': target.name,
                          'input_seconds': seconds, 'expected_reply': expected_reply,
                          'expected_media': 'sound:parent-marks/' + expected.stem})
        result['languages'][language] = {
            'digit': {'en': '1', 'hi': '2', 'kn': '3'}[language],
            'identity_prompt_media': 'sound:parent-marks/' + prompt_audio.stem,
            'steps': steps,
        }
    (directory / 'fixtures.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Prepared synthetic parent speech and independent record-grounded expected replies for English, Hindi and Kannada.')


if __name__ == '__main__':
    main()
