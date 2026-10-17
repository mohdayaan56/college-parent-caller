# Record-grounded LLM question answering

Every non-empty recognized input now reaches the local LLM: language selection,
identity replies, questions, acknowledgements and requests to end the call.
Keypad selection and identity/record authorization remain checked by the app.
Silence is handled as a timeout rather than a question.

After verification, the model receives the authorized student records and recent
dialogue. It explains the question's meaning and selects a structured request.
The app checks subjects, record dependencies, targets and scope, then constructs
the answer from the current CSV. The LLM cannot supply its own marks. There is
no model retraining and no fixed-question lookup that bypasses the LLM.

Supported questions include individual/all/remaining subject marks, totals,
percentages, named subsets, averages, highest/lowest subjects, comparisons,
marks needed to reach a target, threshold counts, class/section and recorded
teacher contacts. Compound questions and contextual follow-ups are supported.
The three total-marks examples in the request have the same answer: 342/400.
The demo's all-subject answer includes Mathematics 85, Science 91, English 78,
and Computer 88. Mohammed Ayaan and zulkharnain remain the demo names.

Attendance, fees, rank, passing policy and other absent facts are reported as
unavailable. An unclear question or model failure asks for clarification instead
of guessing. Records are not given to the model before parent verification;
records belonging to a different parent sharing a phone are excluded.

## Measurement and scope

`tests/fixtures/record_qa_questions.json` contains 127 English/Hindi/Kannada text
questions, including the user's examples, paraphrases, compound questions,
follow-ups, calculations, unavailable facts and call controls. The evaluator
uses the actual local model and checks request meaning, quantities and subjects
in the final answer. Results, model/code/corpus hashes and LLM latency are saved
in `runtime/deployment/local-sip/qa-evaluation/latest.json`.

This is a development regression corpus used while improving the implementation.
Its pass rate is not an independent estimate of real-world accuracy, a guarantee
of 99% on arbitrary questions, or a measurement of human accents and microphones.
LLM latency excludes speech recognition, silence detection, speech synthesis and
handset delay. Real calls save their separate stage timings with `llm_used`,
`llm_seconds` and `requested_kinds` in the call-latency reports.

The active local model remains `gemma4-e2b-college:qat4` on the RTX 4060. Context
is 4096 tokens; warm-up scripts use that same context to avoid a model reload
on the first question.

## Running the tests

For a manual MicroSIP call on this PC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-LocalSipDemo.ps1
```

For automatic conversations in all three languages, with the scripted parent
voice sent through the softphone microphone and both voices audible:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-NativeAudioTest.ps1
```

Use `-Language en`, `-Language hi` or `-Language kn` for one automatic call.
See [NATIVE_AUDIO_TEST.md](NATIVE_AUDIO_TEST.md) for routing and recordings.
The test checks actual PBX playback against independently prepared record-based
answers; the real recognizer and LLM still process the incoming audio.

For the actual-model text accuracy suite:

```powershell
docker exec college-parent-portable-caller-1 mkdir -p /tmp/qa-validation/tests
docker cp tests/. college-parent-portable-caller-1:/tmp/qa-validation/tests/
docker exec -e PYTHONPATH=/tmp/qa-validation:/workspace college-parent-portable-caller-1 python -m scripts.evaluate_record_qa
```

These commands run from the project directory. They read the fictional demo
record. The text evaluator originates no call.
