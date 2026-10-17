from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import unicodedata
from uuid import uuid4

from app.config import settings
from app.csv_store import CsvError, StudentRecord, find_by_phone
from app.intents import classify_intent
from app.llm import extract_name_with_llm, generate_grounded_reply, refine_intent_with_llm, classify_identity_reply
from app.language import phrase, subject_label
from app.verify import extract_spoken_name, names_match, normalize_name


class CallState(str, Enum):
    AWAITING_LANGUAGE = "awaiting_language"
    AWAITING_NAME = "awaiting_name"
    VERIFIED = "verified"
    DENIED = "denied"
    ENDED = "ended"


GREETING = phrase("en", "greeting", college=settings.college_name)
DENY_MESSAGE = (
    "I'm sorry, I could not verify your identity. "
    "For privacy reasons I cannot share any student information. Goodbye."
)
CSV_FAIL = (
    "I'm sorry, the student records are unavailable right now. "
    "I cannot share any marks. Please try again later. Goodbye."
)
STT_FAIL = "I could not hear that clearly. Please say that again."


def marks_summary(student: StudentRecord, language: str = "en") -> str:
    parts = [
        phrase(language, "mark", subject=subject_label(language, name), score=score,
               maximum=settings.max_marks_per_subject)
        for name, score in student.marks.items()
    ]
    subjects = ", ".join(parts)
    return phrase(
        language, "marks", student=student.student_name, subjects=subjects,
        total=student.total, max_total=student.max_total, percentage=student.percentage,
    )


def with_followup(reply: str, language: str) -> str:
    # Clarification replies already ask a question; avoid consecutive prompts.
    return reply if reply.rstrip().endswith('?') else reply + ' ' + phrase(language, 'anything_else')


def reply_for_intent(student: StudentRecord, intent: dict, language: str = "en") -> str:
    return with_followup(_intent_answer(student, intent, language), language)


def _intent_answer(student: StudentRecord, intent: dict, language: str = "en") -> str:
    kind = intent.get("intent")
    if kind == "other_student":
        return phrase(language, "other_student")
    if kind == "all_subjects":
        subjects = ", ".join(phrase(language, "mark", subject=subject_label(language, name),
                                   score=score, maximum=settings.max_marks_per_subject)
                             for name, score in student.marks.items())
        return phrase(language, "all_subjects", student=student.student_name, subjects=subjects)
    if kind == "subject":
        subject = intent.get("subject")
        score = student.subject_score(subject or "")
        if score is None:
            return phrase(language, "which_subject", subjects=", ".join(
                subject_label(language, name) for name in student.marks))
        return phrase(
            language, "subject", student=student.student_name, score=score,
            maximum=settings.max_marks_per_subject, subject=subject_label(language, subject),
        )
    if kind == "percentage":
        return phrase(language, "percentage", student=student.student_name, percentage=student.percentage)
    if kind == "total":
        return phrase(language, "total", student=student.student_name,
                      total=student.total, max_total=student.max_total)
    if kind == "performance":
        return phrase(language, "performance", student=student.student_name,
                      total=student.total, max_total=student.max_total,
                      percentage=student.percentage)
    if kind == "lowest_subject":
        lowest = min(student.marks.values())
        subjects = ", ".join(subject_label(language, name) for name, score in student.marks.items()
                             if score == lowest)
        return phrase(language, "lowest_subject", student=student.student_name,
                      subjects=subjects, score=lowest, maximum=settings.max_marks_per_subject)
    if kind == "class_info":
        return phrase(language, "class", student=student.student_name,
                      class_name=student.class_name, section=student.section)
    if kind == "data_unavailable":
        return phrase(language, "data_unavailable")
    if kind == "out_of_scope":
        return phrase(language, "out_of_scope")
    if kind == "contact":
        if not student.teacher_name or not student.teacher_phone:
            return phrase(language, "no_contact")
        return phrase(language, "contact", teacher=student.teacher_name, phone=student.teacher_phone)
    if kind == "repeat":
        return marks_summary(student, language)
    return phrase(language, "clarify")


def rewrite_intent_reply(student, intent, language, utterance='', *, cached_only=False):
    reply = _intent_answer(student, intent, language)
    if not settings.llm_generate_replies or intent['intent'] not in {'subject', 'percentage', 'total', 'performance'}:
        return with_followup(reply, language)
    facts = reply
    if intent['intent'] == 'performance':
        facts = phrase(language, 'performance_core', student=student.student_name,
                       total=student.total, max_total=student.max_total, percentage=student.percentage)
    required_term = (subject_label(language, intent['subject'])
                     if intent['intent'] == 'subject' and intent.get('subject') else '')
    try:
        generated = generate_grounded_reply(facts, utterance, language, student.student_name,
                                           required_term, cached_only=cached_only)
    except Exception:
        generated = None
    if generated:
        return with_followup(generated + (' ' + phrase(language, 'performance_limit') if intent['intent'] == 'performance' else ''), language)
    return with_followup(reply, language)


@dataclass
class TurnResult:
    session_id: str
    state: CallState
    reply: str
    hangup: bool = False
    verified: bool = False


@dataclass
class CallSession:
    expected_phone: str
    records: list[StudentRecord] | None = None
    session_id: str = field(default_factory=lambda: uuid4().hex)
    state: CallState = CallState.AWAITING_NAME
    verify_attempts: int = 0
    wards: list[StudentRecord] = field(default_factory=list)
    csv_ok: bool = True
    csv_error: str | None = None
    was_verified: bool = False
    opted_out: bool = False
    language: str = "en"
    active_student_id: str = ""
    confirmation_prompted: bool = False
    language_attempts: int = 0
    last_answer_reply: str = ""
    dialogue_history: list[dict] = field(default_factory=list)
    last_subjects: list[str] = field(default_factory=list)
    last_requests: list[dict] = field(default_factory=list)
    last_llm_seconds: float = 0.0
    last_interpretation: str = ''
    last_llm_used: bool = False
    authorized_parent_names: set[str] = field(default_factory=set)

    @property
    def transcription_language(self) -> str:
        # Meta CTC is multilingual without a language hint. Using "en" here
        # avoids rejecting a Hindi/Kannada script before the choice is known.
        return "en" if self.state == CallState.AWAITING_LANGUAGE else self.language

    def _parent_label(self) -> str:
        row = self.wards[0]
        return getattr(row, f"parent_name_{self.language}", "") or row.parent_name

    def greeting(self) -> TurnResult:
        try:
            self.wards = self.records if self.records is not None else find_by_phone(self.expected_phone)
            self.csv_ok = True
            if self.wards:
                self.language = self.wards[0].language
        except CsvError as exc:
            self.csv_ok = False
            self.csv_error = str(exc)
            self.state = CallState.ENDED
            return TurnResult(
                self.session_id, self.state, phrase(self.language, "csv_fail"), hangup=True, verified=False
            )
        if settings.ask_language_before_identity:
            self.state = CallState.AWAITING_LANGUAGE
            self.confirmation_prompted = False
            return TurnResult(self.session_id, self.state,
                              phrase(self.language, "choose_language", college=settings.college_name))
        return self._confirmation_prompt()

    def _confirmation_prompt(self) -> TurnResult:
        self.state = CallState.AWAITING_NAME
        self.confirmation_prompted = bool(self.wards) and len({
            normalize_name(row.parent_name) for row in self.wards
        }) == 1
        greeting = (phrase(self.language, "confirm_parent", college=settings.college_name,
                           parent=self._parent_label()) if self.confirmation_prompted
                    else phrase(self.language, "greeting", college=settings.college_name))
        return TurnResult(self.session_id, self.state, greeting, hangup=False)

    def handle_text(self, utterance: str, use_llm: bool | None = None) -> TurnResult:
        self.last_llm_used = False
        self.last_llm_seconds = 0.0
        self.last_interpretation = ''
        self.last_requests = []
        if use_llm is None:
            use_llm = settings.ollama_enabled
        if self.state in {CallState.DENIED, CallState.ENDED}:
            return TurnResult(self.session_id, self.state, "Goodbye.", hangup=True)

        if not self.csv_ok:
            self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state, phrase(self.language, "csv_fail"), hangup=True)

        text = (utterance or "").strip()
        if not text:
            key = 'language_retry' if self.state == CallState.AWAITING_LANGUAGE else 'stt_fail'
            ended = False
            if self.state == CallState.AWAITING_LANGUAGE:
                self.language_attempts += 1
                ended = self.language_attempts >= 3
                if ended:
                    self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state, phrase(self.language, key), hangup=ended)
        if (not any(unicodedata.category(char)[0] in {'L','N'} for char in text)
                and not (self.state == CallState.AWAITING_LANGUAGE and settings.language_menu_keypad_only)):
            return TurnResult(self.session_id, self.state, phrase(self.language, "stt_fail"), hangup=False)

        if use_llm and settings.llm_understand_every_input:
            return self._handle_llm_turn(text)

        if classify_intent(text)["intent"] == "opt_out":
            self.opted_out = True
            self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state, phrase(self.language, "opt_out"), hangup=True)

        if self.state == CallState.AWAITING_LANGUAGE:
            from app.language_choice import language_choices
            selected = (sorted(language_choices(text))
                        if not settings.language_menu_keypad_only or text in {'1','2','3'} else [])
            if len(selected) == 1:
                self.language = selected[0]
                return self._confirmation_prompt()
            self.language_attempts += 1
            if self.language_attempts >= 3:
                self.state = CallState.ENDED
                return TurnResult(self.session_id, self.state,
                                  phrase(self.language, "language_retry"), hangup=True)
            return TurnResult(self.session_id, self.state, phrase(self.language, "language_retry"))

        if self.state == CallState.AWAITING_NAME:
            return self._verify(text, use_llm=use_llm)

        subjects = list(self.wards[0].marks) if self.wards else []
        # Conversational acknowledgements are not requests for results. Avoid
        # asking the optional intent model to turn them into marks questions.
        acknowledgement = ''.join(c for c in text.casefold()
                                  if not unicodedata.category(c).startswith('P')).strip()
        if acknowledgement in {'yes','yeah','okay','ok','sure','i have some other questions',
                               'i have a question','i have another question','हाँ','ಹೌದು'}:
            student = next((r for r in self.wards if r.student_id == self.active_student_id),self.wards[0])
            return TurnResult(self.session_id,self.state,
                              phrase(self.language,'ask_question',student=student.student_name),verified=True)
        # Exact replies to the follow-up. A negative followed by a new question
        # ("No, tell me the science marks") must continue the conversation.
        closure = ' '.join(''.join(c for c in text.casefold()
                                   if not unicodedata.category(c).startswith('P')).split())
        end_answer = closure in {
            'no', 'no more questions', 'no other questions', 'nothing', 'that is all',
            'नहीं', 'नही', 'नहीं और कुछ नहीं', 'कुछ नहीं', 'कॉल समाप्त करें',
            'ಇಲ್ಲ', 'ಇನ್ನೇನೂ ಇಲ್ಲ', 'ಬೇರೆ ಪ್ರಶ್ನೆ ಇಲ್ಲ', 'ಕರೆ ಮುಗಿಸಿ',
        }
        if end_answer or classify_intent(text, subjects)["intent"] == "goodbye":
            self.state = CallState.ENDED
            parent = self.wards[0].parent_name if self.wards else ""
            bye = phrase(self.language, "bye", parent=parent)
            return TurnResult(self.session_id, self.state, bye, hangup=True, verified=True)

        try:
            self.wards = self.records if self.records is not None else find_by_phone(self.expected_phone)
        except CsvError:
            self.csv_ok = False
            self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state, phrase(self.language, "csv_fail"), hangup=True, verified=True)

        intent = classify_intent(text, subjects)
        if use_llm and intent["intent"] == "clarify":
            try:
                llm_intent = refine_intent_with_llm(text, subjects)
            except Exception:
                llm_intent = None
            if llm_intent:
                intent = llm_intent
        if intent["intent"] == "opt_out":
            self.opted_out = True
            self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state,
                              phrase(self.language, "opt_out"), hangup=True, verified=True)
        if intent["intent"] == "goodbye":
            self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state,
                              phrase(self.language, "bye", parent=self.wards[0].parent_name if self.wards else ""),
                              hangup=True, verified=True)
        if not self.wards:
            self.state = CallState.ENDED
            return TurnResult(self.session_id, self.state, phrase(self.language, "csv_fail"), hangup=True, verified=True)

        selected = self._select_student(text)
        student = next((row for row in self.wards if row.student_id == self.active_student_id), self.wards[0])
        if selected and intent["intent"] == "clarify":
            self.last_answer_reply = ""
            return TurnResult(self.session_id,self.state,
                              phrase(self.language,'ask_question',student=student.student_name),verified=True)
        if selected:
            self.last_answer_reply = ""
        if intent['intent'] == 'repeat' and not re.search(
                r'\b(marks?|scores?|results?|summary)\b|अंक|ಅಂಕ',text,re.IGNORECASE):
            reply = self.last_answer_reply or phrase(self.language,'ask_question',student=student.student_name)
            return TurnResult(self.session_id,self.state,reply,verified=True)
        reply = (rewrite_intent_reply(student, intent, self.language, text,
                                      cached_only=settings.require_prepared_call_audio) if use_llm
                 else reply_for_intent(student, intent, self.language))
        if intent['intent'] in {'subject','all_subjects','total','percentage','performance','lowest_subject',
                                'class_info','contact','repeat'}:
            self.last_answer_reply = reply
        return TurnResult(
            self.session_id, self.state, reply, hangup=False, verified=True
        )

    def _handle_llm_turn(self, text):
        from time import monotonic
        from app import record_qa
        started = monotonic()
        self.last_llm_used = True
        self.last_requests = []
        self.last_interpretation = ''
        try:
            if self.state == CallState.AWAITING_LANGUAGE:
                choice = record_qa.understand_menu(text)
                # Exact controls must win over model mistakes, including a
                # hallucinated opt-out. The input has still reached the LLM.
                keypad_choice = {'1': 'en', '2': 'hi', '3': 'kn'}.get(text)
                if keypad_choice:
                    choice = keypad_choice
                if choice == 'opt_out':
                    self.opted_out = True
                    self.state = CallState.ENDED
                    return TurnResult(self.session_id, self.state, phrase(self.language, 'opt_out'), hangup=True)
                if choice == 'goodbye':
                    self.state = CallState.ENDED
                    return TurnResult(self.session_id, self.state, phrase(self.language, 'bye', parent=''), hangup=True)
                # Keypad controls remain exact, but their input still reaches the LLM.
                if settings.language_menu_keypad_only:
                    choice = keypad_choice
                if choice in {'en', 'hi', 'kn'}:
                    self.language = choice
                    return self._confirmation_prompt()
                self.language_attempts += 1
                ended = self.language_attempts >= 3
                if ended:
                    self.state = CallState.ENDED
                return TurnResult(self.session_id, self.state, phrase(self.language, 'language_retry'), hangup=ended)
            if self.state == CallState.AWAITING_NAME:
                names = [name for row in self.wards for name in
                         (row.parent_name, getattr(row, f'parent_name_{self.language}', '')) if name]
                decision = classify_identity_reply(text, names)
                if classify_intent(text)['intent'] == 'opt_out':
                    self.opted_out = True
                    self.state = CallState.ENDED
                    return TurnResult(self.session_id, self.state, phrase(self.language, 'opt_out'), hangup=True)
                return self._verify(text, use_llm=True, identity_decision=decision)
            try:
                self.wards = self.records if self.records is not None else find_by_phone(self.expected_phone)
                if self.authorized_parent_names:
                    self.wards = [row for row in self.wards
                                  if normalize_name(row.parent_name) in self.authorized_parent_names]
            except CsvError:
                self.wards = []
            if not self.wards:
                self.state = CallState.ENDED
                return TurnResult(self.session_id, self.state, phrase(self.language, 'csv_fail'), hangup=True)
            if self.active_student_id not in {row.student_id for row in self.wards}:
                self.active_student_id = self.wards[0].student_id
            plan = record_qa.understand_question(text, self.wards, self.active_student_id,
                                                self.language, self.dialogue_history, self.last_subjects)
            if plan is None:
                return TurnResult(self.session_id, self.state, phrase(self.language, 'understanding_fail'), verified=True)
            requests = plan['requests']
            self.last_interpretation = plan.get('interpretation', '')
            self.last_requests = requests
            if any(request['kind'] == 'opt_out' for request in requests):
                self.opted_out = True
                self.state = CallState.ENDED
                return TurnResult(self.session_id, self.state, phrase(self.language, 'opt_out'), hangup=True, verified=True)
            if plan['student_id'] != self.active_student_id:
                self.last_answer_reply = ''
                self.last_subjects = []
            self.active_student_id = plan['student_id']
            student = next(row for row in self.wards if row.student_id == self.active_student_id)
            closing = any(request['kind'] == 'goodbye' for request in requests)
            answers = [record_qa.answer_request(student, request, self.language,
                                               self.last_answer_reply, self.last_subjects)
                       for request in requests if request['kind'] != 'goodbye']
            if closing:
                self.state = CallState.ENDED
                reply = ' '.join(answers + [phrase(self.language, 'bye', parent=student.parent_name)])
            else:
                reply = with_followup(' '.join(answers), self.language)
            self.last_requests = requests
            for request in requests:
                if request['kind'] == 'all_subjects':
                    self.last_subjects = list(student.marks)
                elif request['kind'] == 'remaining_subjects':
                    excluded = request['subjects'] or self.last_subjects
                    self.last_subjects = [key for key in student.marks if key not in excluded]
                elif request['subjects']:
                    self.last_subjects = request['subjects']
                elif request['kind'] in {'total', 'percentage', 'average'}:
                    self.last_subjects = list(student.marks)
            if any(request['kind'] not in {'acknowledge', 'repeat', 'clarify', 'goodbye', 'data_unavailable',
                                            'other_student', 'out_of_scope'} for request in requests):
                self.last_answer_reply = reply
            self.dialogue_history.append({'parent': text[:400], 'assistant': reply[:700], 'requests': requests})
            self.dialogue_history = self.dialogue_history[-3:]
            return TurnResult(self.session_id, self.state, reply, hangup=closing, verified=True)
        finally:
            self.last_llm_seconds = round(monotonic() - started, 3)

    def _select_student(self, utterance: str) -> bool:
        normalized = normalize_name(utterance)
        tokens = set(normalized.split())
        for row in self.wards:
            names = [row.student_name, getattr(row, f"student_name_{self.language}", "")]
            full_match = any(name and normalize_name(name) in normalized for name in names)
            first = normalize_name(row.student_name).split()[0]
            unique_first = sum(
                normalize_name(other.student_name).split()[0] == first for other in self.wards
            ) == 1
            if full_match or (unique_first and first in tokens):
                changed = self.active_student_id != row.student_id
                self.active_student_id = row.student_id
                return changed
        return False

    def _verify(self, utterance: str, use_llm: bool, identity_decision=None) -> TurnResult:
        uncertain = bool(re.search(r'\b(maybe|perhaps|probably|not sure|i think|i guess|might be)\b',utterance,re.IGNORECASE)
                         or any(word in utterance for word in ('शायद','ಬಹುಶಃ')))
        if not uncertain and (re.search(r"\b(no|not|wrong person)\b", utterance, re.IGNORECASE) or any(
            word in utterance for word in ("नहीं", "नही", "ಅಲ್ಲ", "ಇಲ್ಲ")
        )):
            self.state = CallState.DENIED
            return TurnResult(self.session_id, self.state, phrase(self.language, "deny"), hangup=True)
        spoken = extract_spoken_name(utterance)
        answer = re.sub(r"[^\w\s\u0900-\u097f\u0c80-\u0cff]", " ", utterance.casefold())
        answer = " ".join(answer.split()).strip("।")
        affirmative = answer in {
            "yes", "yes i am", "yes it is me", "yes this is me", "yes speaking",
            "yes that's me", "yes that s me", "yes i am speaking", "speaking",
            "that's me", "that s me", "that is me", "that's right", "that s right",
            "that is correct", "that's correct", "that s correct", "correct",
            "this is he", "this is she", "it s me", "you have the right person",
            "हाँ", "हां", "जी", "जी हाँ", "जी हां", "हाँ मैं हूँ", "हां मैं हूं",
            "जी मैं ही हूँ", "ಹೌದು", "ಹೌದು ನಾನೇ", "ಹೌದು ನಾನು", "ನಾನೇ",
            "जी हाँ मैं ही हूँ", "जी हां मैं ही हूं", "हाँ मैं ही हूँ", "हां मैं ही हूं",
            "जी यहाँ मैं ही हूँ", "ಹೌದು ನಾನೆ",
        }
        other_person = bool(re.search(r"\b(i know|looking for|can i (?:speak|talk) to)\b|"
                                      r"['’]s (?:friend|brother|sister|son|daughter|wife|husband)\b",
                                      utterance,re.IGNORECASE))
        matched = ([] if uncertain or other_person else list(self.wards) if self.confirmation_prompted and affirmative
                   else _match_parent(spoken, self.wards, self.language))
        if use_llm and not matched and not uncertain and not other_person and self.confirmation_prompted:
            names = [name for row in self.wards for name in
                     (row.parent_name,getattr(row,f'parent_name_{self.language}','')) if name]
            decision = identity_decision if identity_decision is not None else classify_identity_reply(utterance,names)
            if decision == 'confirmed':
                matched = list(self.wards)
            elif decision == 'denied':
                self.state = CallState.DENIED
                return TurnResult(self.session_id,self.state,phrase(self.language,'deny'),hangup=True)
        elif use_llm and not matched and not uncertain and not other_person:
            try:
                spoken = extract_name_with_llm(utterance) or spoken
            except Exception:
                pass
            matched = _match_parent(spoken, self.wards, self.language)
        if matched:
            self.wards = matched
            self.authorized_parent_names = {normalize_name(row.parent_name) for row in matched}
            self.state = CallState.VERIFIED
            self.was_verified = True
            student = matched[0]
            self.active_student_id = student.student_id
            extra = ""
            if len(matched) > 1:
                names = ", ".join(row.student_name for row in matched)
                extra = " " + phrase(self.language, "multiple", students=names, student=student.student_name)
            intro = phrase(self.language, "intro", parent=student.parent_name, student=student.student_name)
            reply = f"{intro}{extra} " + phrase(self.language,'ask_question',student=student.student_name)
            return TurnResult(
                self.session_id, self.state, reply, hangup=False, verified=True
            )

        self.verify_attempts += 1
        if self.verify_attempts >= settings.max_verify_attempts:
            self.state = CallState.DENIED
            return TurnResult(
                self.session_id, self.state, phrase(self.language, "deny"), hangup=True, verified=False
            )
        return TurnResult(
            self.session_id,
            self.state,
            (phrase(self.language, "confirm_retry", parent=self._parent_label())
             if self.confirmation_prompted else phrase(self.language, "name_retry")),
            hangup=False,
            verified=False,
        )


def _match_parent(spoken: str, wards: list[StudentRecord], language: str = "en") -> list[StudentRecord]:
    return [
        row for row in wards
        if names_match(spoken, getattr(row, f"parent_name_{language}", "") or row.parent_name)
        or names_match(spoken, row.parent_name)
    ]


_sessions: dict[str, CallSession] = {}


def create_session(phone: str, records: list[StudentRecord] | None = None) -> CallSession:
    session = CallSession(expected_phone=phone, records=records)
    _sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> CallSession | None:
    return _sessions.get(session_id)


def close_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
