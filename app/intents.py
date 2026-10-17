from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.csv_store import SUBJECT_ALIASES, SUBJECTS


GOODBYE_RE = re.compile(
    r"\b(goodbye|good bye|bye|hang up|hangup|that's all|thats all|"
    r"nothing else|no thanks|no thank you|end the call)\b",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"\b(percent|percentage|overall)\b", re.IGNORECASE)
TOTAL_RE = re.compile(
    r"\b(total|grand total|out of|aggregate|combined marks|sum of marks|cumulative marks|"
    r"add (?:up )?(?:all |the )?(?:(?:his|her|their|my child's) )?(?:marks|scores)(?: together)?)\b",
    re.IGNORECASE,
)
ROMANIZED_TOTAL_RE = re.compile(r"\bottu\b", re.IGNORECASE)
LOWEST_RE = re.compile(
    r"\b(needs? improvement|needs? to improve|weakest subject|lowest (?:score|marks?)|"
    r"which subject.*improv)\b",
    re.IGNORECASE,
)
PERFORMANCE_RE = re.compile(
    r"\b(how (?:is|are) (?:my |the )?(?:child|son|daughter|student|ward|he|she|they) doing|"
    r"academic (?:progress|performance)|(?:his|her|their|my child's) (?:progress|performance|grades)|"
    r"how (?:has|is) (?:my |the )?(?:child|son|daughter|student|ward) (?:performed|performing)|"
    r"how are (?:his|her|their) results)\b",
    re.IGNORECASE,
)
REPEAT_RE = re.compile(r"\b(repeat|again|all (the )?marks|full (result|marks)|summary)\b", re.IGNORECASE)
ALL_SUBJECTS_RE = re.compile(
    r"\b(?:all|every|each|individual|separate)(?:\s+(?:of|the))*\s+subjects?\b|"
    r"\bsubject(?:[- ]wise|[- ]by[- ]subject)\b|"
    r"(?:सभी|सारे|हर|प्रत्येक)\s*(?:विषय|सब्जेक्ट)|विषय[ -]?वार|सब्जेक्ट\s*वाइज|"
    r"अलग[ -]?अलग\s*(?:विषय|सब्जेक्ट)|"
    r"(?:ಎಲ್ಲಾ|ಎಲ್ಲ|ಪ್ರತಿ|ಪ್ರತಿಯೊಂದು)\s*(?:ವಿಷಯ|ಸಬ್ಜೆಕ್ಟ್)|ವಿಷಯವಾರು",
    re.IGNORECASE,
)
LOCAL_ALL_MARKS_RE = re.compile(
    r"(?:सभी|सारे|पूरे)\s*(?:अंक|मार्क)|(?:ಎಲ್ಲಾ|ಎಲ್ಲ)\s*(?:ಅಂಕ|ಮಾರ್ಕ್)",
    re.IGNORECASE,
)
CLASS_RE = re.compile(r"\b(class|section|standard|grade)\b", re.IGNORECASE)
CONTACT_RE = re.compile(r"\b(teacher|faculty|contact|phone number|call whom)\b", re.IGNORECASE)
DATA_UNAVAILABLE_RE = re.compile(
    r"\b(fees?|transport|bus|behaviou?r|attendance|attend(?:ing|s)?|regularly|"
    r"(?:come|comes|go|goes) to class|present in class|"
    r"syllabus|rank|ranking|topper|hostel|scholarship|exams?|timetable|"
    r"teacher (?:feedback|comments?|observations?))\b",
    re.IGNORECASE,
)
UNSUPPORTED_ACTION_RE = re.compile(
    r"\b(whatsapp|send (?:me )?(?:a )?(?:message|sms))\b", re.IGNORECASE
)
OTHER_STUDENT_RE = re.compile(
    r"\b(another|other|different|unrelated) student(?:'s)?\b|दूसरे छात्र|दूसरी छात्रा|"
    r"ಬೇರೆ ವಿದ್ಯಾರ್ಥಿ|ಇನ್ನೊಬ್ಬ ವಿದ್ಯಾರ್ಥಿ",
    re.IGNORECASE,
)
LOCAL_CONTACT = ("शिक्षक", "अध्यापक", "संपर्क", "शिक्षिका", "ಶಿಕ್ಷಕ", "ಸಂಪರ್ಕ")
LOCAL_DATA_UNAVAILABLE = ("फीस", "व्यवहार", "हाज़िरी", "उपस्थिति", "फೀಸ್", "बाकी बच्चों से", "कक्षा में स्थान", "परीक्षा",
                      "रोज़ क्लास", "रोज क्लास", "प्रतिदिन कक्षा", "नियमित रूप से कक्षा",
                      "ಶುಲ್ಕ", "ಹಾಜರಾತಿ", "ವರ್ತನೆ", "ಮೊದಲನೇ ಸ್ಥಾನ", "ಬೇರೆಯವರಿಗಿಂತ", "ಪರೀಕ್ಷೆ",
                      "ಪ್ರತಿದಿನ ತರಗತಿಗೆ", "ನಿಯಮಿತವಾಗಿ ತರಗತಿಗೆ")
LOCAL_GOODBYE = ("अलविदा", "धन्यवाद", "बस इतना", "ಇಷ್ಟು ಸಾಕು", "ಧನ್ಯವಾದ", "ವಿದಾಯ")
OPT_OUT_RE = re.compile(
    r"\b(stop calling|stop these calls|do not call|don't call|do not ring|don't ring|"
    r"remove my number|opt out)\b",
    re.IGNORECASE,
)
LOCAL_OPT_OUT = ("दोबारा कॉल मत", "फोन मत करना", "कॉल बंद", "मुझे आगे फोन न करें",
                 "ಮತ್ತೆ ಕರೆ ಮಾಡಬೇಡಿ", "ಕರೆ ಮಾಡಬೇಡಿ", "ಈ ಸಂಖ್ಯೆಗೆ ಕರೆ ಮಾಡಬೇಡಿ")
LOCAL_PERCENT = ("प्रतिशत", "शत-प्रतिशत", "ಶೇಕಡಾ", "ಶೇಕಡಾವಾರು")
LOCAL_TOTAL = ("कुल", "ಒಟ್ಟು")
LOCAL_LOWEST = ("सबसे कम अंक", "किस विषय में सुधार", "ಅತ್ಯಂತ ಕಡಿಮೆ ಅಂಕ", "ಕಡಿಮೆ ಅಂಕ", "ಯಾವ ವಿಷಯದಲ್ಲಿ ಸುಧಾರಣೆ")
LOCAL_PERFORMANCE = ("पढ़ाई कैसी", "प्रदर्शन कैसा", "प्रगति कैसी", "कैसा कर रहा", "कैसी कर रही",
                     "ಅಭ್ಯಾಸ ಹೇಗಿದೆ", "ಓದು ಹೇಗಿದೆ", "ಪ್ರಗತಿ ಹೇಗಿದೆ", "ಪ್ರದರ್ಶನ ಹೇಗಿದೆ")
LOCAL_REPEAT = ("दोबारा", "फिर से", "ಮತ್ತೆ", "ಪುನಃ")
LOCAL_CLASS = ("कक्षा", "ತರಗತಿ", "ವಿಭಾಗ")
LOCAL_SUBJECTS = {
    "maths": ("गणित", "मैथ", "ಗಣಿತ"),
    "science": ("विज्ञान", "ಸೈನ್ಸ್", "ವಿಜ್ಞಾನ"),
    "english": ("अंग्रेज़ी", "अंग्रेजी", "ಇಂಗ್ಲಿಷ್"),
    "computer": ("कंप्यूटर", "कम्प्यूटर", "ಕಂಪ್ಯೂಟರ್"),
}


def _words(text):
    # Unicode combining marks belong to the same spoken word as its letters.
    return ''.join(char if unicodedata.category(char)[0] in {'L', 'M', 'N'} else ' '
                   for char in unicodedata.normalize('NFC', text)).split()


def _similar_word(text, choices, minimum=0.85):
    """Conservative typo tolerance for long topic words, never for identity."""
    for word in _words(text):
        for choice in choices:
            if len(choice) >= 5 and abs(len(word)-len(choice)) <= 2:
                if SequenceMatcher(None, word, choice).ratio() >= minimum:
                    return True
    return False


def _speech_subject(text, subjects):
    # A damaged subject name alone is ambiguous. Require a marks/score request
    # and a unique long Latin subject match. Short local names remain exact.
    if not re.search(r'\b(marks?|scores?|scored)\b', text):
        return None
    candidates = set()
    aliases = {**SUBJECT_ALIASES, **{name: SUBJECT_ALIASES.get(name, name) for name in subjects}}
    for alias, canonical in aliases.items():
        if len(alias) < 9 or not alias.isascii():
            continue
        matching = {name for name in subjects if SUBJECT_ALIASES.get(name, name) == canonical}
        if matching and _similar_word(text, [alias], minimum=0.78):
            candidates.update(matching)
    return candidates.pop() if len(candidates) == 1 else None


def classify_intent(utterance: str, subjects: tuple[str, ...] | list[str] = SUBJECTS) -> dict:
    text = (utterance or "").strip()
    lowered = text.lower()
    if not text:
        return {"intent": "clarify", "subject": None}
    if OPT_OUT_RE.search(lowered) or any(word in lowered for word in LOCAL_OPT_OUT):
        return {"intent": "opt_out", "subject": None}
    if GOODBYE_RE.search(lowered) or any(word in lowered for word in LOCAL_GOODBYE):
        return {"intent": "goodbye", "subject": None}
    if OTHER_STUDENT_RE.search(lowered):
        return {"intent": "other_student", "subject": None}
    if (DATA_UNAVAILABLE_RE.search(lowered) or any(word in lowered for word in LOCAL_DATA_UNAVAILABLE)
            or _similar_word(lowered, ['attendance', 'उपस्थिति', 'ಹಾಜರಾತಿ'])):
        return {"intent": "data_unavailable", "subject": None}
    if UNSUPPORTED_ACTION_RE.search(lowered):
        return {"intent": "out_of_scope", "subject": None}
    if LOWEST_RE.search(lowered) or any(word in lowered for word in LOCAL_LOWEST):
        return {"intent": "lowest_subject", "subject": None}

    if ALL_SUBJECTS_RE.search(lowered):
        # Asking for the aggregate across subjects still means a total/percent;
        # otherwise enumerate the CSV rather than selecting its first subject.
        if PERCENT_RE.search(lowered) or any(word in lowered for word in LOCAL_PERCENT):
            return {"intent": "percentage", "subject": None}
        if TOTAL_RE.search(lowered) or any(word in lowered for word in LOCAL_TOTAL):
            return {"intent": "total", "subject": None}
        return {"intent": "all_subjects", "subject": None}

    subject = _find_subject(lowered, subjects) or _speech_subject(lowered, subjects)
    if not subject and LOCAL_ALL_MARKS_RE.search(lowered):
        if PERCENT_RE.search(lowered) or any(word in lowered for word in LOCAL_PERCENT):
            return {"intent": "percentage", "subject": None}
        if TOTAL_RE.search(lowered) or any(word in lowered for word in LOCAL_TOTAL):
            return {"intent": "total", "subject": None}
        return {"intent": "all_subjects", "subject": None}
    if subject and (PERCENT_RE.search(lowered) or "how much" in lowered or "score" in lowered or "marks" in lowered or "scored" in lowered):
        return {"intent": "subject", "subject": subject}
    if subject:
        return {"intent": "subject", "subject": subject}
    if (PERFORMANCE_RE.search(lowered) or any(word in lowered for word in LOCAL_PERFORMANCE)
            or re.search(r'\bhow\b.{0,24}\b(child|son|daughter|student|ward)\b.{0,12}\bdoing\b', lowered)
            or ('ಹೇಗಿದೆ' in lowered and _similar_word(lowered, ['ಪ್ರಗತಿ', 'ಪ್ರದರ್ಶನ'], minimum=0.8))):
        return {"intent": "performance", "subject": None}
    if PERCENT_RE.search(lowered) or any(word in lowered for word in LOCAL_PERCENT):
        return {"intent": "percentage", "subject": None}
    if TOTAL_RE.search(lowered) or ROMANIZED_TOTAL_RE.search(lowered) or any(word in lowered for word in LOCAL_TOTAL):
        return {"intent": "total", "subject": None}
    if REPEAT_RE.search(lowered) or any(word in lowered for word in LOCAL_REPEAT):
        return {"intent": "repeat", "subject": None}
    if CLASS_RE.search(lowered) or any(word in lowered for word in LOCAL_CLASS):
        return {"intent": "class_info", "subject": None}
    if CONTACT_RE.search(lowered) or any(word in lowered for word in LOCAL_CONTACT):
        return {"intent": "contact", "subject": None}
    return {"intent": "clarify", "subject": None}


def _find_subject(text: str, subjects: tuple[str, ...] | list[str]) -> str | None:
    for subject, aliases in LOCAL_SUBJECTS.items():
        if subject in subjects and any(alias in text for alias in aliases):
            return subject
    for alias, canonical in SUBJECT_ALIASES.items():
        matching = next((name for name in subjects if SUBJECT_ALIASES.get(name, name) == canonical), None)
        if matching and re.search(rf"\b{re.escape(alias)}\b", text):
            return matching
    for subject in subjects:
        if re.search(rf"\b{re.escape(subject)}\b", text):
            return subject
    return None
