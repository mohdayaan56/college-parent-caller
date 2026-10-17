# College Parent Caller

Local voice assistant for parent enquiries about a student's recorded marks, with English, Hindi and Kannada conversation, identity confirmation, keypad language selection and speech interruption support.

The assistant sends recognized questions to the local LLM for interpretation and constructs factual answers from the authorized student's CSV record. It supports individual and all-subject marks, totals, percentages, comparisons and related calculations. The demo record is Mohammed Ayaan, parent zulkharnain, with marks 85, 91, 78 and 88 (342/400, 85.5%).

## Set up on another computer

Clone this private repository using your own GitHub account after the owner grants access:

```powershell
git clone https://github.com/mohdayaan56/college-parent-caller.git
cd college-parent-caller
```

Start Docker Desktop with Linux containers and WSL 2. Windows x64, 16 GB RAM and sufficient free disk space are required. For an NVIDIA GPU with at least 10 GiB VRAM:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Check -Profile gpu -Online
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Install -Profile gpu
```

For an 8 GB RTX 4060, append `-AllowLowVram` to both commands. This permits a trial; installation still checks CUDA and available resources. Use `-Profile cpu` for a computer without a suitable NVIDIA GPU. Installation downloads models and generates separate credentials on that computer; it does not place calls.

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for installation and SIP account configuration. That guide records the original portable baseline. The later RTX 4060 demo uses Whisper English recognition plus IndicConformer Hindi/Kannada, VITS voices and Gemma through Ollama; those optional model selectors are in `scripts/` and their guides are linked below. Installing the baseline alone does not reproduce every local demo override.

## Computer call demo

[LOCAL_SIP_DEMO.md](LOCAL_SIP_DEMO.md) describes the existing Windows MicroSIP demo. On a new computer, install a softphone and configure the generated local SIP account first. The convenience script expects portable MicroSIP at `runtime/deployment/demo-softphone/MicroSIP.exe` and the local `PJSIP/zoiper1` route. The executable and its account configuration are deliberately not in Git.

Once configured:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Run-LocalSipDemo.ps1
```

Answer in MicroSIP, press 1 for English, 2 for Hindi or 3 for Kannada, confirm the demo parent's identity, and ask about the student. Local SIP rings a computer softphone. Calling mobile numbers requires a separately configured telephone service or gateway; Bluetooth calling remains experimental.

## Development

Source lives in `app/`, inference and setup tools in `scripts/`, container definitions in `deploy/` and `asterisk/`, and regression tests in `tests/`. Use the fictional sample `data/students.example.csv`; keep actual student records in the ignored runtime directory.

After installation, run the regression tests inside the caller container:

```powershell
docker exec college-parent-portable-caller-1 mkdir -p /tmp/project-tests/tests
docker cp tests/. college-parent-portable-caller-1:/tmp/project-tests/tests/
docker exec -e PYTHONPATH=/tmp/project-tests:/workspace -e OLLAMA_ENABLED=false -e LANGUAGE_MENU_KEYPAD_ONLY=false college-parent-portable-caller-1 python -m unittest discover -s /tmp/project-tests/tests -q
```

The actual model evaluation is described in [RECORD_QA_UPGRADE.md](RECORD_QA_UPGRADE.md); its development corpus does not establish universal 99% accuracy. [NATIVE_AUDIO_TEST.md](NATIVE_AUDIO_TEST.md) covers automated audio calls, and [NUMBER_SPEECH_FIX.md](NUMBER_SPEECH_FIX.md) covers whole-number pronunciation.

For team changes, pull first, create a branch, commit, push and open a pull request:

```powershell
git pull --ff-only
git switch -c feature/my-change
# Edit and verify your changes.
git add app scripts tests
git commit -m "Describe the change"
git push -u origin feature/my-change
```

Git excludes downloaded models, virtual environments, generated deployment files, credentials, real student CSVs, certificates, recordings and local softphone state. Each developer installs their own dependencies and generates their own secrets. Do not commit these local files.
