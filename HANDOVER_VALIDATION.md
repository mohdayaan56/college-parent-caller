# Portable release validation — 8 October 2026

This file records packaging checks, not production approval or RTX latency measurements.

## Changes in this release

- Standalone Docker CPU/GPU build targets, independent of old local experiment images.
- Windows/Linux checking, install, verification, start/stop entry points.
- Pinned model inventory, resumable downloads, SHA256 verification and no host virtual environment dependency.
- Fresh ARI/Zoiper/admin credentials; retained CSV/provider state; separate ports and private campaign database.
- Meta ONNX CUDA inference and VITS ONNX waveform-decoder CUDA inference, requiring observed CUDA kernels.
- Compatible CUDA 12.8/cuDNN 9 PyTorch and ORT versions; bounded LLM context/output and sequential calls.
- Three-language synthetic speech and grounded reply smoke checks, without placing outbound calls.
- Optional project-local trusted CA support; TLS verification stays enabled.
- Explicit package allowlist and release hashes, excluding private runtime, models, real CSVs and certificates.

## Known limits

- This laptop has Intel UHD graphics, not NVIDIA. Actual RTX 4080 execution, GPU memory and handset latency must be checked on the receiving machine.
- Preflight HTTP checks do not guarantee future download availability or OS/driver configuration. Pinned hashes detect changed/corrupt artifacts.
- Model output needs native-speaker review. Synthetic checks do not establish semantic correctness or real telephone ASR accuracy.
- The last Samsung/Airtel Bluetooth test received speech but stalled during playback/hangup before marks. No gateway resets or calls were performed for packaging.
- SIP credentials, authorized caller ID and network configuration must come from the college/provider; an installer cannot generate carrier access.

## Checks performed

| Check | Actual result |
| --- | --- |
| Windows PowerShell entry point | CPU Check with online probes passed; warns about low currently free RAM |
| Selected weights | All 15 pinned files present and SHA256 verified; reused without duplicate model downloads |
| Download access | All 15 artifact URLs reachable; package/PyTorch indexes reachable; expected registry authentication challenge observed |
| Fresh small downloads | All 10 config/tokenizer/source/license files below 1 MB downloaded afresh in memory and matched release hashes |
| Standalone CPU Docker build | Passed, using the project-local trusted inspection CA on this laptop |
| Packaged regression suite | **98/98 passed** from a fresh ZIP extraction in the new CPU image, without original `.env` or virtual environment |
| Voice decoder preparation | English/Hindi/Kannada exports passed numerical equivalence and dynamic-length checks; outputs separate from active workers |
| No-call integration | Passed service readiness, ARI authentication, 401 without admin token, all three TTS generations and 8 kHz ASR inputs, all three LLM fact/script checks and language/affirmation/total caller dialogues |
| CPU/GPU configuration generation | Valid Compose configuration; generated target paths with spaces work; rerun preserves passwords/CSV/provider config |
| GPU failure checks | Tests reject unavailable CUDA, CPU fallback and provider claims without CUDA kernel events. Actual NVIDIA inference not run |

The integration test used the new extracted caller/VITS source and a separate
local PBX, while reusing the existing Meta STT and host Ollama workers to avoid
duplicate resident models on a RAM-constrained laptop. The new standalone CPU
image and its dependencies were built and tested. A complete fresh installation
with the separate Docker Ollama service, fresh STT startup and GPU offload still
needs the receiving machine's Install/Verify run.

Inspecting the replies: English reported Demo Student's total as **342/400**;
Hindi reported **400 में से 342** and Kannada **400ರಲ್ಲಿ 342**. All three raw
model repeat checks preserved the input sentence and its numbers. These were
fictional typed questions, not a human speech acceptance trial.

The synthetic speech roundtrip returned nonempty transcripts, but **recognition
was imperfect**, especially English number words. Hindi/Kannada transcripts also
contained spelling/word-boundary errors. The smoke pass does not mean those
transcripts were correct. Review parent questions and short affirmations on
real telephone audio before deployment. CPU smoke timings were recorded on an
Intel laptop under concurrent service load and are not RTX latency predictions.

Private reports and synthetic WAVs are kept locally under `runtime/deployment/`.
The source ZIP includes no generated secrets or test recordings. It contains a
per-file `RELEASE_MANIFEST.json`; the sibling `.zip.sha256` verifies the archive.
