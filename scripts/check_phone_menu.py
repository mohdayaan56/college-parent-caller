"""No-call check using real TTS and ASR through the deployed language menu."""

import json
import wave

from app.audio_cache import cached_speech
from app.conversation import CallSession, CallState
from app.csv_store import find_by_student_id
from app.dtmf import ToneDigits
from app.stt import transcribe_file


def main():
    student = find_by_student_id('demo-phone-001')
    if student is None:
        raise RuntimeError('Fictional phone demo record missing')
    results = []
    failed = []
    for language, text in [('en','I prefer English'), ('hi','मुझे हिंदी में बात करनी है'), ('kn','ನನಗೆ ಕನ್ನಡದಲ್ಲಿ ಮಾತನಾಡಬೇಕು')]:
        path = cached_speech(text,language)
        with wave.open(str(path),'rb') as audio:
            pcm = audio.readframes(audio.getnframes())
            assert audio.getframerate() == 8000 and audio.getsampwidth() == 2
        false_digits = []
        ToneDigits(false_digits.append).feed(pcm)
        assert not false_digits, f'Speech incorrectly detected as keypad input: {false_digits}'
        transcript = transcribe_file(path,'en',constrain_script=True,language_menu=True)
        session = CallSession(expected_phone=student.parent_phone,records=[student])
        session.greeting()
        turn = session.handle_text(transcript,use_llm=False)
        result = {'spoken_language':language,'transcript':transcript,
                  'selected_language':session.language,'state':session.state.value,
                  'verified':turn.verified}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if session.language != language or session.state != CallState.AWAITING_NAME or turn.verified:
            failed.append(language)
    if failed:
        raise AssertionError('Language choice did not reach the identity prompt safely: '+str(failed))
    session = CallSession(expected_phone=student.parent_phone,records=[student])
    session.greeting()
    session.handle_text('English',use_llm=False)
    path = cached_speech('Yes.','en')
    transcript = transcribe_file(path,'en')
    turn = session.handle_text(transcript,use_llm=False)
    print(json.dumps({'identity_transcript':transcript,'state':session.state.value,
                      'verified':turn.verified},ensure_ascii=False),flush=True)
    if not turn.verified:
        raise AssertionError('English confirmation failed with explicit English decoding')
    print('PASS: all three spoken choices reached identity; speech produced no false keypad digits')


if __name__ == '__main__':
    main()
