# Local SIP demo on this PC

Marks and totals now use whole-number pronunciation; 342/400 is spoken as
"three hundred and forty-two out of four hundred" in English, with native
Hindi/Kannada equivalents. See [NUMBER_SPEECH_FIX.md](NUMBER_SPEECH_FIX.md).

Questions now go through the LLM before record-grounded answers are constructed.
See [RECORD_QA_UPGRADE.md](RECORD_QA_UPGRADE.md) for scope and accuracy measurements.

For automatic English/Hindi/Kannada calls when the physical microphone is unavailable,
see [NATIVE_AUDIO_TEST.md](NATIVE_AUDIO_TEST.md). The helper plays prerecorded
parent speech through Stereo Mix into a separate MicroSIP profile, verifies the
actual PBX responses, and restores the normal softphone afterward.

MicroSIP is registered as `zoiper1` with the local Docker Asterisk PBX. The caller
uses `PJSIP/zoiper1`, ARI at `http://pbx:8088/ari`, and internal RTP to `caller`.
SIP/RTP ports remain bound to Windows loopback. This rings MicroSIP on this PC;
it does not dial the CSV's mobile number or use the iPhone's SIM/Bluetooth.

Run a demo from PowerShell:

```powershell
cd 'C:\Users\mohda\Downloads\College-Parent-Caller-2026-10-08\Cursor-MajorProject'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-LocalSipDemo.ps1
```

Answer in MicroSIP. Use its dialpad: 1 for English, 2 for Hindi, 3 for Kannada.
Confirm naturally that you are zulkharnain, then ask about Mohammed Ayaan in the demo record.
The demo total is 342/400, or 85.5%. Use the PC microphone and speakers or a headset.
Say goodbye or click Hang up when finished. The script checks the active local route,
registration and idle channels before placing a call.
It warms the local language model before dialing and allows up to 240 seconds
for cold model startup and audio preparation. If the model cannot start, the
script exits before placing a call. Live data is in
`runtime/deployment/students.csv`; the fresh-install sample has the same demo
names. Identity validation and the softphone display name read the active CSV.

## Revalidation on 13 October 2026

Both live demo records now name Mohammed Ayaan and zulkharnain, with Hindi and
Kannada pronunciation fields updated. Existing scores and call targets are retained.
149 regression tests passed in the installed Docker environment. The synthetic
capture test now allows three seconds for its queued packet burst to drain on a
busy laptop; the production interruption settings remain 0.75 VAD and 320 ms.
The actual local model passed all eight natural identity cases; five multilingual
all-subject requests returned all four marks with the new student name and speech.
The synthetic real-ASR interruption test retained the question and returned 342/400;
onset to interruption took 3.774 seconds under current memory pressure. Real PBX
playback stopped 0.058 seconds after cancellation. These automated results do not
establish microphone or speaker quality in a human conversation.
Docker's startup failure was resolved by preserving its stale IPC socket folders
and recreating the runtime paths. Images, volumes, and student data were retained.

The subsequent live MicroSIP demo, session
`2fb8c074aa9d4b4d82ac42de92fb4213`, accepted keypad 1 and "Yes", then
recognized "Go to the marks", "What is total?", "What's the total percentage?",
and "Thank you". A spoken question during playback stopped it 0.033 seconds
after ASR confirmed speech. Keypad handling to identity playback took 0.172 seconds;
estimated end-of-speech to question-answer playback ranged from 1.666 to 3.902
seconds at the caller/PBX boundary. These exclude handset delay. The call ended
with StasisEnd and zero active channels. Its latency measurements are saved in
`runtime/deployment/local-sip/latest-call-latency.json`. The user confirmed that
the live demo used Mohammed Ayaan and zulkharnain, read all four subjects
correctly, and sounded clear.

## Interruption behavior

Breath/noise previously could stop playback after about 160 ms of low-threshold VAD
activity. Playback now requires at least 320 ms of consecutive high-confidence VAD
frames (0.75 threshold) and a local ASR result containing spoken words. Non-speech
labels, punctuation-only results and failed recognition do not authorize stopping.
The audio continues buffering during the recognition check, including 400 ms of
pre-roll, so the parent's question onset is retained. Incoming listening after a
reply finishes retains the existing endpointing behavior. These are general speech
checks; no fixed question vocabulary or word training is used.

Breath evidence from playback is also cleared when the reply ends, so it does not
become a stale empty answer and trigger an immediate repeat prompt. The user
confirmed the updated call looked correct after a live Hindi conversation.

This adds recognition delay to interruption. A synthetic "What is the total?"
test with real VAD/ASR took 1.403 seconds from speech onset to interruption; it kept
the question, returned 342/400, and ended on "No". Actual Asterisk playback stopped
within 0.028 seconds after cancellation. Human breath/noise rejection still needs
confirmation on the PC microphone; VAD/ASR can misclassify unusual sounds.

## Validation

The first local SIP call received digit 1 as an ARI event, advanced to English
identity, transcribed "Yes", subsequent questions and "Bye", then hung up normally.
Digit handling to identity playback measured 0.103 seconds at the application/PBX
boundary. The user confirmed the menu advanced but reported chopped audio, then
identified breath-triggered interruptions. The stricter interruption guard above
was added in response. 48 affected tests passed, including rejecting non-speech,
preserving the onset, native-script words and unchanged keypad selection.

Subsequent calls using MicroSIP's documented command-line keypad verified digit 2
selected Hindi and digit 3 selected Kannada through actual ARI events. Handling to
identity playback took 0.090 and 0.098 seconds respectively. Each automated call
was ended after the identity prompt; these were keypad tests, not complete Hindi
or Kannada conversations. Results are in `runtime/deployment/local-sip/keypad-checks.json`.

Route backups are under `runtime/deployment/local-sip/`. The voice models, CSV and
slightly slower speech remain unchanged. `Run-PhoneDemo.ps1` now refuses to run
while the caller uses local SIP, to avoid reporting the wrong call route.

## All subject marks correction

Requests for all subjects, each subject or subject-wise marks now use an
`all_subjects` intent. The reply enumerates every subject and score from the active
student's CSV record. It includes no aggregate total or percentage unless those
are requested. One-subject questions still use `subject`, and an unqualified
"repeat" repeats the previous answer. Native Hindi/Kannada requests for all marks
are supported, and the model's general interpretation distinguishes listing every
subject from asking for their sum.

62 affected tests passed. The no-call `scripts/check_all_subjects.py` check used
the actual local model and speech service: five English/Hindi/Kannada requests,
including the Hindi transcripts from the live call, returned all four demo scores
and prepared their voice audio. Results are in
`runtime/deployment/local-sip/all-subjects-check.json`. The fixed response has not
yet been checked in another live conversation. Restarting only the caller applies
the correction; the local demo command above remains the same.
