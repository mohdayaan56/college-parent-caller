# Interrupting answers and ending the call

Verified parents can speak during the marks summary or subsequent replies.
Incoming-only RTP feeds the same speech capture throughout playback and the
following listening period. Five consecutive voice frames (160 ms) trigger
interruption; the caller stops the active playback and abandons the remaining
reply chunks. A 400 ms audio pre-roll retains the question onset. Silence during
a long reply does not use the parent's answer timeout or grow the ASR buffer.

Factual answers end with “Do you have any other questions, or shall we end the
call?” Equivalent prompts are prepared in Hindi and Kannada. Replies that
already ask for clarification keep their existing question. An exact “No”,
“नहीं”, or “ಇಲ್ಲ” ends a verified conversation; “No, tell me the science marks”
continues it. Identity confirmation remains required before sharing marks.

Interruption requires the streaming RTP mode. A recording fallback continues
the ordinary turn-by-turn call if streaming media fails. Language selection,
identity prompts, and final goodbye playback retain their existing behavior.

## Verification on this RTX 4060 deployment

The automated audio check generated an English total-marks question with VITS,
sent telephone PCMU through the receiver, and used the actual Silero VAD and GPU
ASR service. The transcript was `what is the total`, the answer was 342/400, and
the subsequent “No” ended the conversation. In the latest run, simulated bot
playback was interrupted 0.284 seconds after the synthetic question's audio
began. This is a smoke test, not a human-call latency guarantee.

A separate real Asterisk test played to a temporary UDP media channel, received
audio, cancelled playback, and observed PlaybackFinished 0.017 seconds later.
It did not ring the softphone. The implementation uses Asterisk's documented
[DELETE playback operation](https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Asterisk_REST_Interface/Playbacks_REST_API/).

The initial synthetic science question was transcribed as “signce”, which the
conservative intent parser did not recognize without LLM refinement. These
checks do not establish ASR accuracy for arbitrary questions. A live handset
interruption test is still pending, as is the previously deferred Hindi call.

Results: `runtime/deployment/barge-in-check.json`.

All 73 targeted regression tests passed, including the full call-loop capture
handoff, playback cancellation race, bounded pre-roll, cached audio, and
multilingual follow-up/identity handling. The updated caller is running and all
73 demo audio entries have been prepared in the three languages.

## Run the automated audio and Asterisk checks

From PowerShell in this project folder:

```powershell
docker compose -f runtime/deployment/compose.json exec -T caller python -m scripts.check_barge_in --pbx
```

Omit `--pbx` to exercise only synthetic incoming audio and simulated bot playback.

## Start a live demo call

Keep the existing MicroSIP demo account registered. These commands read the local
admin token without displaying it, prepare all prompts, and ring the local
softphone using the fictional demo student:

```powershell
$demoCredentials = Get-Content runtime/deployment/credentials.json -Raw | ConvertFrom-Json
$demoHeaders = @{'X-Admin-Token' = $demoCredentials.admin_token}
$demoBody = '{"student_id":"demo-001"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8113/calls/prepare -Headers $demoHeaders -ContentType 'application/json' -Body $demoBody -TimeoutSec 180
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8113/calls/originate -Headers $demoHeaders -ContentType 'application/json' -Body $demoBody
```

Answer, select keypad 1, and confirm the zulkharnain identity. While the marks
summary is speaking, ask “What is the total?” The summary should stop and the
caller should answer the new question. At the follow-up prompt, say “No” to
end. Headphones help prevent speaker audio feeding back into the microphone.
