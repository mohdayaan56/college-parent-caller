"""Quick no-call probe of the user's examples against the actual local model."""
import json
from app.csv_store import find_by_student_id
from app.record_qa import understand_question, answer_request


def main():
    student = find_by_student_id('demo-001')
    for text in ['Please tell me the marks of all the subjects.',
                 'What is his score in Mathematics?', "What's his marks in total.",
                 'Tell me his total marks.', 'His total marks, can you tell me.',
                 'What is the difference between maths and science marks?',
                 'How many marks does he need in maths to reach 90?',
                 'What are the total marks and overall percentage?']:
        plan = understand_question(text, [student], student.student_id, 'en', [], [])
        answers = [answer_request(student, request, 'en') for request in plan['requests']] if plan else []
        print(json.dumps({'question': text, 'plan': plan, 'answers': answers}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
