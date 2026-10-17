import unittest
from dataclasses import replace
from unittest.mock import patch

from app.config import settings
from app.conversation import CallSession, CallState
from app.csv_store import load_students
from app.record_qa import answer_request, validate_plan, understand_question
from tests.support import CsvFixtureMixin


def request(kind, subjects=(), value=None, unit='marks', inclusive=False):
    return dict(kind=kind, subjects=list(subjects), value=value, unit=unit, inclusive=inclusive)


class RecordQaTests(CsvFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        settings.llm_understand_every_input = True
        self.student = load_students()[0]

    def session(self):
        session = CallSession(expected_phone=self.student.parent_phone, records=[self.student])
        session.greeting()
        with patch('app.conversation.classify_identity_reply', return_value='confirmed') as model:
            self.assertTrue(session.handle_text('Yes', use_llm=True).verified)
            model.assert_called_once()
        return session

    def test_all_three_total_paraphrases_reach_the_model_and_return_the_record(self):
        session = self.session()
        for question in ("What's his marks in total", 'Tell me his total marks', 'His total marks, can you tell me?'):
            plan = dict(student_id=self.student.student_id, requests=[request('total')])
            with patch('app.record_qa.llm._chat_json', return_value=plan) as model:
                result = session.handle_text(question, use_llm=True)
                model.assert_called_once()
                self.assertIn(question, model.call_args.args[0])
                self.assertIn('342 out of 400', result.reply)
                self.assertTrue(session.last_llm_used)

    def test_acknowledgement_closure_and_known_subject_do_not_bypass_llm(self):
        session = self.session()
        for text, kind, subjects in [('okay', 'acknowledge', []), ('mathematics score', 'subject', ['maths']), ('No', 'goodbye', [])]:
            with patch('app.record_qa.llm._chat_json', return_value=dict(student_id=self.student.student_id,
                       requests=[request(kind, subjects)])) as model:
                result = session.handle_text(text, use_llm=True)
                model.assert_called_once()
                if kind == 'acknowledge':
                    self.assertNotIn('85', result.reply)
                elif kind == 'subject':
                    self.assertIn('85 out of 100', result.reply)
                else:
                    self.assertTrue(result.hangup)

    def test_keypad_input_reaches_llm_before_exact_menu_selection(self):
        with patch.object(settings, 'ask_language_before_identity', True), patch.object(settings, 'language_menu_keypad_only', True):
            session = CallSession(expected_phone=self.student.parent_phone, records=[self.student])
            session.greeting()
            with patch('app.record_qa.llm._chat_json', return_value={'choice': 'en'}) as model:
                session.handle_text('2', use_llm=True)
                model.assert_called_once()
                self.assertEqual(session.language, 'hi')

    def test_valid_keypad_cannot_become_a_model_opt_out_or_hangup(self):
        with patch.object(settings, 'ask_language_before_identity', True):
            for keypad_only in (True, False):
                with patch.object(settings, 'language_menu_keypad_only', keypad_only):
                    for digit, language in [('1', 'en'), ('2', 'hi'), ('3', 'kn')]:
                        for wrong_choice in ('opt_out', 'goodbye', 'uncertain', None):
                            session = CallSession(expected_phone=self.student.parent_phone, records=[self.student])
                            session.greeting()
                            with patch('app.record_qa.llm._chat_json', return_value={'choice': wrong_choice}) as model:
                                turn = session.handle_text(digit, use_llm=True)
                                model.assert_called_once()
                            self.assertFalse(turn.hangup)
                            self.assertFalse(session.opted_out)
                            self.assertEqual(session.state, CallState.AWAITING_NAME)
                            self.assertEqual(session.language, language)

    def test_only_parent_names_are_sent_before_verification(self):
        session = CallSession(expected_phone=self.student.parent_phone, records=[self.student])
        session.greeting()
        with patch('app.conversation.classify_identity_reply', return_value='uncertain') as model:
            result = session.handle_text('Tell me his marks', use_llm=True)
            self.assertFalse(result.verified)
            self.assertNotIn('85', result.reply)
            self.assertEqual(model.call_args.args[0], 'Tell me his marks')
            self.assertNotIn('342', str(model.call_args))

    def test_model_outage_does_not_fall_back_to_guessing_a_question(self):
        session = self.session()
        session.last_requests = [request('total')]
        with patch('app.record_qa.llm._chat_json', return_value=None):
            result = session.handle_text('all subjects marks', use_llm=True)
        self.assertIn('ask the question again', result.reply)
        self.assertNotIn('78', result.reply)
        self.assertEqual(session.state, CallState.VERIFIED)
        self.assertEqual(session.last_requests, [])

    def test_opt_out_is_recorded_and_does_not_disclose_marks(self):
        session = self.session()
        with patch('app.record_qa.llm._chat_json', return_value=dict(student_id=self.student.student_id,
                   requests=[request('opt_out')])):
            result = session.handle_text('Do not call me again', use_llm=True)
        self.assertTrue(result.hangup)
        self.assertTrue(session.opted_out)
        self.assertEqual(session.last_requests[0]['kind'], 'opt_out')
        self.assertNotIn('342', result.reply)

    def test_shared_phone_does_not_authorize_a_different_parent(self):
        other = replace(self.student, student_id='unrelated', parent_name='Different Parent',
                        student_name='Other Child', marks={'maths': 1})
        session = CallSession(expected_phone=self.student.parent_phone, records=[self.student, other])
        session.greeting()
        with patch('app.conversation.classify_identity_reply', return_value='confirmed'):
            self.assertTrue(session.handle_text(self.student.parent_name, use_llm=True).verified)
        with patch('app.record_qa.llm._chat_json', return_value=dict(student_id='unrelated',
                   requests=[request('total')])) as model:
            result = session.handle_text('Other Child total marks?', use_llm=True)
        self.assertNotIn('"student_id": "unrelated"', model.call_args.args[0])
        self.assertEqual([row.student_id for row in session.wards], [self.student.student_id])
        self.assertIn('ask the question again', result.reply)

    def test_all_subjects_updates_context_after_a_single_subject(self):
        session = self.session()
        for kind, subjects in [('subject', ['maths']), ('all_subjects', [])]:
            with patch('app.record_qa.llm._chat_json', return_value=dict(student_id=self.student.student_id,
                       requests=[request(kind, subjects)])):
                session.handle_text('question', use_llm=True)
        self.assertEqual(session.last_subjects, list(self.student.marks))

    def test_history_and_record_reach_llm_for_follow_up(self):
        session = self.session()
        with patch('app.record_qa.llm._chat_json', return_value=dict(student_id=self.student.student_id,
                   requests=[request('subject', ['maths'])])):
            session.handle_text('maths marks', use_llm=True)
        with patch('app.record_qa.llm._chat_json', return_value=dict(student_id=self.student.student_id,
                   requests=[request('subject', ['science'])])) as model:
            result = session.handle_text('And science?', use_llm=True)
            self.assertIn('recent_dialogue', model.call_args.args[0])
            self.assertIn('maths marks', model.call_args.args[0])
            self.assertIn('91 out of 100', result.reply)

    def test_multiple_questions_use_independent_grounded_calculations(self):
        session = self.session()
        with patch('app.record_qa.llm._chat_json', return_value=dict(student_id=self.student.student_id,
                   requests=[request('subject', ['maths']), request('percentage')])):
            result = session.handle_text('Maths marks and overall percentage?', use_llm=True)
        self.assertIn('85 out of 100', result.reply)
        self.assertIn('85.5', result.reply)
        self.assertEqual(result.reply.count('other questions'), 1)

    def test_model_cannot_supply_marks_or_access_unlinked_student(self):
        for plan in [dict(student_id='not-linked', requests=[request('total')]),
                     dict(student_id=self.student.student_id, requests=[request('subject', ['unknown'])]),
                     dict(student_id=self.student.student_id, requests=[request('shortfall', value=float('nan'))])]:
            self.assertIsNone(validate_plan(plan, [self.student]))
        plan = dict(student_id=self.student.student_id, requests=[{**request('subject', ['maths']), 'score': 100}])
        safe = validate_plan(plan, [self.student])
        answer = answer_request(self.student, safe['requests'][0], 'en')
        self.assertIn('85 out of 100', answer)

    def test_fact_availability_and_subject_extraction_guard_the_answer(self):
        for item in [{**request('subject', ['maths']), 'asked_subjects': ['history']},
                     {**request('class_info'), 'requires_unrecorded_fact': True}]:
            safe = validate_plan(dict(student_id=self.student.student_id, requests=[item]), [self.student])
            self.assertEqual(safe['requests'][0]['kind'], 'data_unavailable')
            self.assertNotIn('85', answer_request(self.student, safe['requests'][0], 'en'))
        item = {**request('total'), 'asked_subjects': ['total']}
        safe = validate_plan(dict(student_id=self.student.student_id, requests=[item]), [self.student])
        self.assertEqual(safe['requests'][0]['kind'], 'total')
        item = {**request('remaining_subjects', ['science', 'english', 'computer']), 'asked_subjects': []}
        safe = validate_plan(dict(student_id=self.student.student_id, requests=[item]), [self.student])
        answer = answer_request(self.student, safe['requests'][0], 'en', last_subjects=['maths'])
        self.assertNotIn('85', answer)
        self.assertIn('91', answer)
        item = {**request('contact'), 'asked_subjects': ['teacher_phone']}
        safe = validate_plan(dict(student_id=self.student.student_id, requests=[item]), [self.student])
        self.assertEqual(safe['requests'][0]['kind'], 'contact')

    def test_subject_scope_cannot_be_taken_from_the_call_language(self):
        plan = dict(student_id=self.student.student_id, requests=[request('percentage', ['english'])])
        safe = validate_plan(plan, [self.student], utterance='What is his percentage?')
        self.assertEqual(safe['requests'][0]['subjects'], [])
        self.assertIn('85.5', answer_request(self.student, safe['requests'][0], 'en'))
        plan['requests'] = [request('subject', list(self.student.marks))]
        safe = validate_plan(plan, [self.student], utterance='Every paper individually, please')
        self.assertEqual(safe['requests'][0]['kind'], 'all_subjects')
        safe = validate_plan(plan, [self.student], utterance='What is his mathematics score?')
        self.assertEqual(safe['requests'][0]['subjects'], ['maths'])

    def test_remaining_follow_up_uses_the_complement_and_preserves_all_subject_requests(self):
        plan = dict(student_id=self.student.student_id,
                    interpretation='The scores of subjects not mentioned yet.',
                    requests=[request('all_subjects', ['science', 'english', 'computer'])])
        safe = validate_plan(plan, [self.student], utterance='And the other papers?', last_subjects=['maths'])
        self.assertEqual(safe['requests'][0]['kind'], 'remaining_subjects')
        answer = answer_request(self.student, safe['requests'][0], 'en', last_subjects=['maths'])
        self.assertNotIn('85', answer)
        self.assertIn('91', answer)
        plan['interpretation'] = 'The individual scores of all subjects.'
        safe = validate_plan(plan, [self.student], utterance='All subjects please', last_subjects=['maths'])
        self.assertEqual(safe['requests'][0]['kind'], 'all_subjects')
        self.assertIn('85', answer_request(self.student, safe['requests'][0], 'en'))

    def test_missing_information_in_compound_question_does_not_hide_available_marks(self):
        plan = dict(student_id=self.student.student_id,
                    interpretation='Mathematics score and attendance information.',
                    requests=[request('subject', ['maths']), request('data_unavailable')])
        safe = validate_plan(plan, [self.student], utterance='Mathematics marks and attendance?')
        self.assertEqual([item['kind'] for item in safe['requests']], ['subject', 'data_unavailable'])
        plan.update(interpretation='Mathematics score on the exam.', requests=[request('subject', ['maths'])])
        safe = validate_plan(plan, [self.student], utterance='His mathematics exam marks?')
        self.assertEqual(safe['requests'][0]['kind'], 'subject')
        plan.update(interpretation='Percentage in the previously discussed Mathematics subject.',
                    requests=[request('percentage', ['maths'])])
        safe = validate_plan(plan, [self.student], utterance='How about the percent for that one?',
                             last_subjects=['maths'])
        self.assertEqual(safe['requests'][0]['subjects'], ['maths'])

    def test_subset_average_extrema_comparison_targets_and_thresholds(self):
        cases = [(request('total', ['maths', 'science']), ['176', '200']),
                 (request('average', ['maths', 'science']), ['88', '100']),
                 (request('highest_subject'), ['Science', '91']),
                 (request('lowest_subject'), ['English', '78']),
                 (request('compare', ['maths', 'science']), ['85', '91', '6']),
                 (request('shortfall', ['maths'], 90), ['85', '90', '5']),
                 (request('shortfall', value=90, unit='percent'), ['342', '360', '18']),
                 (request('above', value=80), ['3', 'Mathematics', 'Science', 'Computer'])]
        for item, expected in cases:
            with self.subTest(item=item):
                answer = answer_request(self.student, item, 'en')
                for value in expected:
                    self.assertIn(value, answer)

    def test_all_subjects_cannot_be_narrowed_to_first_subject_and_remaining_is_explicit(self):
        answer = answer_request(self.student, request('all_subjects', ['english']), 'en')
        for score in ('85', '91', '78', '88'):
            self.assertIn(score, answer)
        answer = answer_request(self.student, request('remaining_subjects'), 'en', last_subjects=['maths'])
        self.assertNotIn('85', answer)
        self.assertIn('91', answer)

    def test_unknown_policy_and_missing_threshold_are_not_invented(self):
        answer = answer_request(self.student, request('data_unavailable'), 'en')
        self.assertIn('do not have', answer)
        self.assertIn('threshold', answer_request(self.student, request('above'), 'en'))
        self.assertIn('maximum', answer_request(self.student, request('shortfall', ['maths'], 110), 'en'))

    def test_new_answers_localize_in_all_three_languages(self):
        for language in ('en', 'hi', 'kn'):
            for kind in ('average', 'highest_subject', 'shortfall'):
                answer = answer_request(self.student, request(kind), language)
                self.assertIn(self.student.student_name, answer)
                self.assertTrue(answer)
