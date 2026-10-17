# Automatic English, Hindi and Kannada microphone tests

This Windows test uses a second portable MicroSIP profile. A helper plays
prerecorded native parent speech into Windows Stereo Mix, which is selected as
that profile's microphone. The real local SIP call carries this audio to the
PBX and the normal speech recognizer, identity check and conversation handler.
No text is submitted to the call in place of audio.

Run all three languages from this project directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-NativeAudioTest.ps1
```

Both sides are audible by default: the real college assistant speaks through
MicroSIP, and the helper supplies the parent's voice automatically. You do not
need to answer, press a key, or speak. Stereo Mix is muted while the college is
speaking and unmuted only while the helper plays a parent reply. This prevents
the college's own speech from being mistaken for a parent question. It is a
controlled conversation with six English turns and five Hindi/Kannada turns, not an additional
generative language model. Use `-Audible:$false` for quiet automated testing.

Use `-Language en`, `-Language hi` or `-Language kn` to test one language; `-Language both`
retains the Hindi/Kannada pair. The script prepares
speech from the active dataset, warms the local model, checks that no call is
active, and checks the loopback audio level before dialing the local extension.
Its Windows-only dependencies are listed in `scripts/audio-test-requirements.txt`.

Each call automatically answers, presses 1, 2 or 3, confirms identity, asks for all
subject marks, the total and percentage, then says goodbye. It verifies that
real PBX playback completes with the exact expected answer audio at each stage.
English also asks for Mathematics. Expected answers are built directly from the
record and fixed test goals, independently of the model being tested.
The synthetic input text, expected answers, observed playback identifiers and
timings are saved in `runtime/deployment/local-sip/audio-test/result.json`.
The parent input WAVs and a Stereo Mix capture are in the same directory.

Stereo Mix is temporarily unmuted and set to 50%, then its previous mute and
level are restored. In quiet mode, the test profile's speaker output is muted;
in audible mode, microphone input is gated between the participants' turns. The
normal MicroSIP profile is reopened after testing. Its configuration is not
edited. Calls go only to the existing loopback SIP extension.

These are synthetic voice tests. They verify audio routing and the deployed
speech pipeline with the supplied voices, rather than human microphones or
the full range of accents. A failed stage is reported as incomplete rather
than counted as a pass. Short acknowledgements and degraded capture can still
be misrecognized.

The controller uses MicroSIP's documented answer, DTMF and hangup commands:
[MicroSIP documentation](https://www.microsip.org/help#command-line).
Windows mute and level control uses [Pycaw](https://github.com/AndreMiras/pycaw).

## Results on 13 October 2026

Both automatic calls passed all five stages: identity, all subjects, total,
percentage and goodbye. Hindi session `3685a93d6eff44c5914703a5445e783e`
and Kannada session `c9499a4247734173907ffcd314466540` played the expected
response WAVs through the real PBX after recognizing microphone-input speech.
The scores were Maths 85, Science 91, English 78 and Computer 88, with
342/400 and 85.5%. Loopback capture had no clipped samples in the preflight.
Full call recordings were saved under `audio-test/received-calls`.

The first attempt exposed a Kannada identity-model issue: it classified the
response as confirmed but copied the registered name into its extracted-name
field even though the reply only said "I am speaking". The existing validation
rejected that invented name. The prompt now asks for name extraction before
status, and both real Kannada self-confirmation cases passed. All eight natural
identity checks and 37 conversation/identity regression tests passed; question,
uncertainty and wrong-person responses remain blocked.

Hindi's short synthetic goodbye was misrecognized in the first attempt.
The successful repeat used a fuller ordinary goodbye and an unclipped input
level. This is a repeatable fixture test, not evidence that every short reply
or accent will be recognized. The normal MicroSIP instance, Stereo Mix's
previous 100% level and its previous muted state were restored. The PBX had
zero active channels at completion.

The user clarified that both participants should speak automatically and be
audible on the PC. A subsequent audible repeat passed all five stages in Hindi
(`5c3a42deb89d4564ba8a3fa9eda72ccb`) and Kannada
(`1ae3d83c75cc4fb9bb8c866a0da95333`). The test softphone's college output was
100%; microphone input was muted during college speech and enabled during the
scripted parent's speech. No human input was required. Both calls ended and
the normal softphone and Stereo Mix settings were restored. The current report
has `audible_both_parties: true`; the labeled full recordings are
`audio-test/hi-full-call.wav` and `audio-test/kn-full-call.wav`.
