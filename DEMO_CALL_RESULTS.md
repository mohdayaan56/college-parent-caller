# Local demo call — 8 October 2026

A real SIP call connected from Docker Asterisk to the registered MicroSIP
softphone on this PC. The user answered, selected English, confirmed the
fictional Demo Parent identity, asked about marks and percentage, and ended
the conversation. The user confirmed: "Audio and answers were clear."

Hardware: NVIDIA RTX 4060 Laptop GPU, 8 GB VRAM. The running GPU deployment
used Meta CTC ASR, Gemma via Ollama, and VITS speech. No telephone carrier or
real parent was called. The demo data totals 342/400, or 85.5%.

## Measured call latency

Twelve incoming speech turns reached a reply playback event.

| Metric | Seconds |
| --- | ---: |
| Median speech end to playback estimate | 1.486 |
| Minimum | 1.323 |
| Maximum | 2.259 |
| Nearest-rank p95 (only 12 observations) | 2.259 |

These are estimates from incoming RTP speech end to the Asterisk ARI
PlaybackStarted event, including the one-second silence endpoint. They
exclude the softphone's output-device delay. All twelve first reply chunks
were cached; these figures do not measure uncached full TTS generation.
Some turns also performed intent preparation through the LLM. No lost RTP
samples were reported. The call ended and Asterisk had zero active channels.

ASR spelling and recognition errors remain visible in the log, including an
initial Chinese transcript and "engles". This successful English demo does
not establish recognition accuracy or live-call quality in Hindi/Kannada.

Measurements: `runtime/deployment/demo-call-latency.json`.
Call log: `runtime/deployment/demo-call.log`.

## Fixes verified for the demo

- Translate the default "the college" phrase before Hindi/Kannada speech
  generation. Previously the leftover English "the" caused HTTP 422 and
  prevented preparing the complete call.
- Use bandlimited polyphase resampling for phone audio. A regression test
  preserves a 1 kHz tone and attenuates a 6 kHz tone by over 40 dB before
  conversion to 8 kHz, preventing aliasing into the speech band.
- Include the phone-conversion implementation in the speech cache fingerprint
  so cached audio is regenerated after conversion changes.

All 73 prepared call replies generated successfully in all three languages.
Twelve targeted tests passed in the deployment containers: two pronunciation
tests, one resampling test, and nine audio-cache/streaming tests. The full
repository test suite was not run as part of this live-call verification.

## Language-menu fix and live verification

Subsequent calls exposed a language-selection failure. Meta ASR transcribed
Kannada as "canada"/"canara", or returned unrelated and mixed scripts.
The strict menu parser rejected these strings and ended the call after
three failed selections.

The menu now accepts the observed Latin variants within language selection
only. It also offers a keypad path: 1 for English, 2 for Hindi, 3 for Kannada.
Asterisk's DTMF event races with speech capture; a valid keypad choice cancels
the capture cleanly and advances to identity confirmation. Keypad selection
cannot verify identity, and digits are ignored after leaving the menu.
Unrelated and ambiguous spoken selections are still rejected. Broader
multilingual speech-recognition accuracy remains unresolved.

Forty-one conversation and ARI regression tests passed. All 73 call replies
were ready after regenerating the changed prompts. In verification session
`2d4bc3f8475d4c4891a554d08c47f195`, Asterisk received keypad 3, the caller
selected `kn`, and the Kannada identity prompt started playing 0.055 seconds
after the keypad choice was handled. This verifies the menu transition;
it is not a measurement of the complete Kannada conversation.
