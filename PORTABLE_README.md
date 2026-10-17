# College parent voice caller

Read **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for installation, RTX GPU configuration,
local credentials, Zoiper/SIP setup, CSV campaigns and troubleshooting.
[HANDOVER_VALIDATION.md](HANDOVER_VALIDATION.md) records what was verified for this release.

Windows RTX laptop quick start, after opening Docker Desktop with Linux/WSL 2:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Check -Profile gpu -Online
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Install -Profile gpu
```

Use `-Profile cpu` without a supported NVIDIA GPU. On Linux with Docker/Compose
and NVIDIA Container Toolkit installed: `bash setup.sh install gpu`.

The installer downloads only the selected pinned models, creates new local
passwords, builds standalone Docker images, exports the VITS decoders and runs
three-language checks. It does not place calls. Open `http://127.0.0.1:8113/docs`
after installation. No host Python environment or prebuilt experiment image is needed.

## Selected stack

- **STT:** Meta omnilingual CTC 300M v2, FP32 ONNX.
- **Response wording:** local Gemma 4 E2B text QAT GGUF through Ollama.
- **TTS:** VoiceAPI VITS English, Hindi and Kannada female voices.
- **Phone media:** Asterisk ARI/PJSIP, compatible with Zoiper and a separately provisioned SIP trunk.
- **Facts and access:** Python dialogue rules, CSV arithmetic, parent affirmation,
  guarded generated wording and missing-data/opt-out responses.

The archive excludes model weights, actual student data, recordings, TLS
certificates and credentials. Downloads total about 4.92 GB before Ollama's
import copy, derived exports and Docker images. Use the resource checker on the
receiving machine before installing.

## Deployment status

RTX 4080 execution is supported by the configuration and checked at runtime;
it has not been measured on this Intel-only development laptop. Complete a
controlled full call before launching batches. The experimental Samsung SIM
Bluetooth route last stalled during playback/hangup and remains unresolved;
use the college PBX/SIP trunk route for deployment.

This portable source release contains the selected pipeline and its regression
tests. The development checkout separately retains historical benchmark reports
and optional backend scripts. The setup guide supersedes earlier experiment commands.
