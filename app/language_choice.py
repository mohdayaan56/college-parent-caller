"""Recognize explicit language choices without changing caller identity state."""

import re
import unicodedata

PATTERNS = {
    'en': r'\b(?:english|inglish|englis|engles|ingles)\b|अंग्रेज़ी|अंग्रेजी|इंग्लिश|ಇಂಗ್ಲಿಷ್|ಇಂಗ್ಲೀಷ್',
    'hi': r'\bhindi\b|हिंदी|हिन्दी|ಹಿಂದಿ',
    'kn': r'\b(?:kannada|kannad|canada|canara)\b|कन्नड़|कन्नड|ಕನ್ನಡ',
}


def language_choices(text):
    text = unicodedata.normalize('NFKC',text).casefold()
    # Ordinary stretched vowel spellings should not require a special phrase.
    # This only normalizes menu text; it never changes identity or answers.
    text = re.sub(r'([aeiou])\1+',r'\1',text)
    if text.strip() in {'1','2','3'}:
        return {{'1':'en','2':'hi','3':'kn'}[text.strip()]}
    return {code for code, pattern in PATTERNS.items()
            if re.search(pattern,text,re.IGNORECASE)}
