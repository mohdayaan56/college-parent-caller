"""Whole-number speech for marks; exact decimal fractions and phone digits."""
import re

DIGITS = {
    'en': 'zero one two three four five six seven eight nine'.split(),
    'hi': 'शून्य एक दो तीन चार पाँच छह सात आठ नौ'.split(),
    'kn': 'ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು'.split(),
}
POINT = {'en': 'point', 'hi': 'दशमलव', 'kn': 'ದಶಮಾಂಶ'}
MINUS = {'en': 'minus', 'hi': 'माइनस', 'kn': 'ಮೈನಸ್'}
PLUS = {'en': 'plus', 'hi': 'प्लस', 'kn': 'ಪ್ಲಸ್'}
EN_SMALL = DIGITS['en'] + 'ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
EN_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
HI_SMALL = (
    'शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस '
    'बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस '
    'तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस '
    'चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास '
    'पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ '
    'साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर '
    'सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी '
    'अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सतासी अट्ठासी नवासी '
    'नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे'
).split()
KN_SMALL = DIGITS['kn'] + 'ಹತ್ತು ಹನ್ನೊಂದು ಹನ್ನೆರಡು ಹದಿಮೂರು ಹದಿನಾಲ್ಕು ಹದಿನೈದು ಹದಿನಾರು ಹದಿನೇಳು ಹದಿನೆಂಟು ಹತ್ತೊಂಬತ್ತು'.split()
KN_TENS = ['', '', 'ಇಪ್ಪತ್ತು', 'ಮೂವತ್ತು', 'ನಲವತ್ತು', 'ಐವತ್ತು', 'ಅರವತ್ತು', 'ಎಪ್ಪತ್ತು', 'ಎಂಭತ್ತು', 'ತೊಂಬತ್ತು']
KN_ENDINGS = ['', 'ೊಂದು', 'ೆರಡು', 'ಮೂರು', 'ನಾಲ್ಕು', 'ೈದು', 'ಾರು', 'ೇಳು', 'ೆಂಟು', 'ೊಂಬತ್ತು']
KN_HUNDREDS = ['', 'ನೂರು', 'ಇನ್ನೂರು', 'ಮುನ್ನೂರು', 'ನಾನೂರು', 'ಐನೂರು', 'ಆರುನೂರು', 'ಏಳುನೂರು', 'ಎಂಟುನೂರು', 'ಒಂಬೈನೂರು']
SCALES = {
    'en': [(10**12, 'trillion'), (10**9, 'billion'), (10**6, 'million'), (1000, 'thousand')],
    'hi': [(10**11, 'खरब'), (10**9, 'अरब'), (10**7, 'करोड़'), (10**5, 'लाख'), (1000, 'हज़ार')],
    'kn': [(10**7, 'ಕೋಟಿ'), (10**5, 'ಲಕ್ಷ'), (1000, 'ಸಾವಿರ')],
}


def cardinal(number, language):
    if number < 0:
        return MINUS[language] + ' ' + cardinal(-number, language)
    for value, word in SCALES[language]:
        if number >= value:
            count, remainder = divmod(number, value)
            result = cardinal(count, language) + ' ' + word
            if remainder:
                joiner = ' and ' if language == 'en' and remainder < 100 else ' '
                result += joiner + cardinal(remainder, language)
            return result
    if number >= 100:
        hundreds, remainder = divmod(number, 100)
        if language == 'kn':
            result = KN_HUNDREDS[hundreds]
            return result[:-1] + ' ' + cardinal(remainder, language) if remainder else result
        result = cardinal(hundreds, language) + (' hundred' if language == 'en' else ' सौ')
        return result + (' and ' if language == 'en' else ' ') + cardinal(remainder, language) if remainder else result
    if language == 'hi':
        return HI_SMALL[number]
    if language == 'kn':
        if number < 20:
            return KN_SMALL[number]
        tens, units = divmod(number, 10)
        return KN_TENS[tens][:-1] + KN_ENDINGS[units] if units else KN_TENS[tens]
    if number < 20:
        return EN_SMALL[number]
    tens, units = divmod(number, 10)
    return EN_TENS[tens] + ('-' + EN_SMALL[units] if units else '')


def _number(text, language):
    sign = ''
    if text.startswith(('-', '+')):
        sign = (MINUS if text[0] == '-' else PLUS)[language] + ' '
        text = text[1:]
    integer, dot, fraction = text.partition('.')
    # Phone numbers and zero-padded identifiers are sequences, not quantities.
    if len(integer) >= 7 or (len(integer) > 1 and integer.startswith('0')):
        spoken = ' '.join(DIGITS[language][int(char)] for char in integer)
    else:
        spoken = cardinal(int(integer), language)
    if dot:
        spoken += ' ' + POINT[language] + ' ' + ' '.join(DIGITS[language][int(char)] for char in fraction)
    return sign + spoken


def normalize_numbers(text, language):
    text = re.sub(r'\d', lambda match: str(int(match[0])), text)
    # A fraction used as a marks total has a natural denominator-first order
    # in Hindi/Kannada; normal caller replies already have localized wording.
    def ratio(match):
        score, maximum = match.groups()
        if language == 'hi':
            return maximum + ' में से ' + score
        if language == 'kn':
            return maximum + 'ರಲ್ಲಿ ' + score
        return score + ' out of ' + maximum
    text = re.sub(r'(?<![\d\w])([0-9]+)\s*/\s*([0-9]+)(?![0-9])', ratio, text)
    # Keep grouped international phone numbers together for digit-wise reading.
    def phone(match):
        digits = re.sub(r'[^0-9]', '', match[0])
        return PLUS[language] + ' ' + ' '.join(DIGITS[language][int(char)] for char in digits)
    text = re.sub(r'(?<!\w)\+[0-9][0-9 ()-]{5,}[0-9](?![0-9])', phone, text)
    def speak(match):
        result = _number(match[0], language)
        if language == 'kn' and text[match.end():].startswith('ರಲ್ಲಿ') and result.endswith('ು'):
            result = result[:-1]
        return result
    return re.sub(r'(?<![A-Za-z0-9.])[-+]?[0-9]+(?:\.[0-9]+)?', speak, text)
