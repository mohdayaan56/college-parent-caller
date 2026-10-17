# Hindi and Kannada call recognition

The previous Meta CTC 300M recognizer frequently decoded Hindi and Kannada
answers into unrelated scripts, preventing identity confirmation. Restricting
its output alphabet did not fix the underlying acoustic errors. A local
Whisper large-v3-turbo GPU evaluation also failed a live Kannada confirmation.
Neither attempt was accepted as a successful fix.

The deployed recognizer now routes English to the original Meta model and
Hindi/Kannada to language-specific IndicConformer 120M CTC ONNX models.
It selects the native model from the caller's language choice, uses the
exporter's specified log-mel preprocessing, and verifies actual CUDA kernels.
Native model residency is bounded to one Hindi/Kannada model at a time.

Original models: [AI4Bharat IndicConformer](https://github.com/AI4Bharat/IndicConformerASR).
ONNX export and preprocessing: [Sulabh Katiyar's public export](https://huggingface.co/sulabhkatiyar/indicconformer-120m-onnx),
revision `16f1697d0631c9bb88e371fca24577efe0aa8617`, licensed CC BY 4.0.
The exported models are adaptations of AI4Bharat's models. This deployment
adds phone resampling, GPU verification, bounded residency, and API routing.
All downloaded files are checked against pinned upstream hashes.

The identity parser also accepts natural Hindi confirmation phrases and a
Kannada spelling variant. Negative and uncertain answers remain rejected.
Keypad 1/2/3 chooses English/Hindi/Kannada only; it cannot verify identity.

## Evidence

- 69 affected preprocessing, conversation, live transport, service, and setup
  regression tests passed in the deployment container.
- Synthetic native ASR recognized Kannada total/percentage questions and
  denials correctly; isolated synthetic affirmative syllables remain unreliable.
- In the live Kannada demo, the recognizer correctly transcribed `ಹೌದು ನಾನೇ`,
  advanced identity confirmation, and the user confirmed it recognized and
  answered. This is a controlled demo, not a general accuracy evaluation.
- The Hindi call reached its identity prompt after recognizing `हिंदीमे`,
  but ended before confirmation. The user chose to test Hindi later. Full
  Hindi call recognition and answer quality remain unverified.
- The opening menu now restricts Meta decoding to Latin labels; the Hindi
  and Kannada names can be pronounced naturally or chosen with keypad 2/3.
  Once selected, native speech uses its language-specific model. The menu
  change is regression-tested, but its spoken accuracy needs further live
  evaluation. Keypad selection was already verified in real calls.
- First measured Kannada speech response began approximately 1.30 seconds
  after estimated speech end, including one-second endpoint silence.
- GPU use during native testing was approximately 4.56 GiB out of 8 GiB.

The existing `runtime/deployment/compose.json` contains the new ASR selection.
Start it with the same command as before:

```powershell
docker compose -f runtime/deployment/compose.json up -d
```

Running a fresh `setup.ps1 -Action Install` regenerates the original Meta
configuration. To reselect already-downloaded native models afterward, run
`python -m scripts.select_indic_demo` from a writable setup container and
recreate the STT service. The original configuration is retained privately
at `runtime/deployment/compose.meta-before-whisper.json`.

Raw synthetic comparisons are in `runtime/deployment/indic-decoding-check.json`;
the failed Meta and Whisper comparisons are retained in adjacent files.
Full native-language synthetic comparisons are retained separately in
`runtime/deployment/indic-decoding-check.native-all.json`. Call evidence is in
`runtime/deployment/indic-live-calls.log` and `indic-live-latency.json`.
