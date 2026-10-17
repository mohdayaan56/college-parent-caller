# Whole-number pronunciation

The voice service now reads marks, totals and class numbers as complete numbers.
For example, 342 out of 400 becomes:

- English: three hundred and forty-two out of four hundred.
- Hindi: चार सौ में से तीन सौ बयालीस.
- Kannada: ನಾನೂರರಲ್ಲಿ ಮುನ್ನೂರ ನಲವತ್ತೆರಡು.

85.5% reads as eighty-five point five percent, पचासी दशमलव पाँच प्रतिशत,
or ಎಂಭತ್ತೈದು ದಶಮಾಂಶ ಐದು ಶೇಕಡಾ. The integer part uses a cardinal number;
fractional decimal digits retain their exact precision. Phone numbers and
zero-padded identifiers continue to use individual digits.

`scripts/number_speech.py` contains the offline conversion, and the native VITS
service applies it before synthesis. Its source is included in the speech-cache
fingerprint, so old audio with digit-wise marks is not reused after this change.
The voice and caller services were reloaded with no active call.

On 14 October 2026, 49 number, voice-normalization, cache and conversation checks
passed. `scripts/check_whole_number_speech.py` generated real total and percentage
audio in all three languages from the active demo dataset. The checked text and
WAV files are under `runtime/deployment/local-sip/whole-number-audio`.
The normal demo commands use the updated voice conversion.
