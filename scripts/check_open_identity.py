"""No-call evaluation of natural identity replies with the deployed local model."""
import json
from app.llm import classify_identity_reply
from app.csv_store import find_by_student_id


def main():
    student = find_by_student_id('demo-001')
    if student is None:
        raise RuntimeError('Demo student missing from the active CSV')
    names = [name for name in (student.parent_name, student.parent_name_hi,
                               student.parent_name_kn) if name]
    cases = [('en','You reached the right person, go ahead.','confirmed'),
             ('hi','जी बिल्कुल, आप मुझसे ही बात कर रहे हैं।','confirmed'),
             ('kn','ನಾನೇ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ, ಹೇಳಿ.','confirmed'),
             ('en','What school are you calling from?','uncertain'),
             ('hi','आप किस कॉलेज से बोल रहे हैं?','uncertain'),
             ('kn','ನೀವು ಯಾವ ಕಾಲೇಜಿನಿಂದ ಕರೆ ಮಾಡುತ್ತಿದ್ದೀರಿ?','uncertain'),
             ('en','I am Someone Else.','denied'),
             ('en',f'I know {student.parent_name}, but I am their friend.','denied')]
    failed = []
    for language,text,expected in cases:
        status = classify_identity_reply(text,names)
        result = {'language':language,'reply':text,'status':status,'expected':expected}
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if status != expected:
            failed.append(result)
    if failed:
        raise AssertionError('Identity classifier needs review: '+json.dumps(failed,ensure_ascii=False))


if __name__ == '__main__':
    main()
