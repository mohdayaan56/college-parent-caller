# RTX 4060 8 GB latency trial

This machine reports an RTX 4060 Laptop GPU with 8,188 MiB total VRAM.
The default setup requires at least 10 GiB; an explicit experimental switch
permits an 8 GB trial without relaxing actual CUDA execution verification.
Model fit and speed must be measured, not inferred from the GPU name.

## Measured results — 8 October 2026

The GPU installation and three-language no-call verification passed on this
RTX 4060 Laptop GPU. STT and all three TTS decoders executed CUDA kernels.
Ollama reported 36/36 model layers offloaded to GPU. The VITS text frontend
remains on CPU as designed.

Ten warm sequential turns were measured per language, after a separate first
turn. Times are HTTP wall time in seconds, including transfer to local services.

| Language | STT median | Gemma median | TTS median | Total median | Total p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| English | 0.050 | 0.527 | 0.199 | 0.791 | 0.841 |
| Hindi | 0.039 | 0.487 | 0.186 | 0.707 | 0.755 |
| Kannada | 0.041 | 0.538 | 0.219 | 0.787 | 0.834 |

Stage medians need not sum to the median of total times. With ten samples,
nearest-rank p95 is the largest observation, so these are coarse percentiles.
Separate first turns took 0.932 / 1.035 / 1.010 seconds respectively; these
are not cold startup times. Gemma's successful initial model load took about
63 seconds; an earlier 45-second caller warmup timed out. Services should be
ready before dialing. Startup is excluded from the warm timing table.

The benchmark is a synthetic component workload, not the production guarded
dialogue or measured phone response delay. It uploads a complete 8 kHz question,
uses one raw Gemma prompt with fixed demo facts, and downloads the complete
uncached synthesized reply. Repeated identical prompts can benefit from
Ollama prompt caching. Real calls use state checks, routing, prepared speech,
partial ASR and first-clause playback with different timing boundaries.

Some synthetic transcripts were incorrect: the English question was decoded
as `atாr the totl mark`, and the Hindi question as `कुई अंत कितनी है`.
The raw fixed-facts prompt still produced the demo reply. Fast inference and
nonempty transcripts do not validate speech accuracy or a real call's outcome.

GPU memory point samples were 4,246 MiB before and 4,358 MiB after the run;
these are not peak measurements. ChatGPT and Chrome, including the active Meet
screen share, were kept open. Nonessential background apps were closed and
free host RAM reached 6.2 GiB before installation. The running services now
consume RAM and VRAM; the free-RAM target is not maintained during inference.

Full samples and backend timings: `runtime/deployment/latency-benchmark.json`.
Three-language GPU verification: `runtime/deployment/verification.json`.

## Install and verify

Close enough applications to leave at least 6 GiB of system RAM free, start
Docker Desktop with its Linux/WSL 2 engine, then run from this project folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Install -Profile gpu -AllowLowVram
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Verify -Profile gpu -AllowLowVram
```

Installation downloads about 4.92 GB of pinned model assets, plus Docker images
and packages. Keep the initial one-call, 2,048-context, 96-output-token settings.
If CUDA runs out of memory, inspect the service logs; a CPU run must be reported
separately. Ollama can partially offload, so inspect its `size_vram` and `size`
in the benchmark report. GPU verification proves some offload, not full offload.

## Measure components without a call

Use any Python 3.11+ interpreter; this benchmark uses only the standard library.
The existing project virtual environment can run it:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_pipeline.py --runs 10
```

The report is `runtime/deployment/latency-benchmark.json`. It records a separate
first turn and ten subsequent turns per language, median, nearest-rank p95,
STT/LLM/TTS HTTP wall times, model timing fields, output lengths, GPU snapshots
and CUDA checks. Ten samples give only a coarse p95; use more runs for a more
stable distribution. Keep power mode, plugged-in status and background GPU
workload consistent when comparing runs. The GPU snapshots are point samples,
not peak-memory measurements; monitor `nvidia-smi -l 1` during execution.

Inputs are synthetic questions resampled to 8 kHz mono PCM16. Each measured
turn uploads the complete audio, calls raw Ollama with fixed trusted demo
facts, then downloads a complete uncached TTS reply. The script does not place
calls or use real student records. Repeated prompts can benefit from Ollama's
prompt cache; this is a repeated-prompt component workload.

These numbers exclude caller intent/identity routing, guarded fallback,
prepared reply caching, streaming partial ASR, first-clause synthesis,
silence endpointing and phone transport. They must not be presented as the
production conversation's speech-end-to-first-audio latency. The first turn
is not a process-cold measurement: service startup and fixture generation
already warm parts of the pipeline.

## Actual conversation latency

For a separately authorized controlled phone test, the caller already writes
`runtime/deployment/recordings/latency.jsonl` and `[CALL LATENCY]` log events.
Inspect `speech_end_to_playback_estimate_seconds` and
`recording_finished_to_playback_seconds`, with their `measurement_boundary`.
Prepared cached replies bypass LLM/TTS generation and need separate results
from uncached turns. The configured silence endpoint is one second. ARI
playback events exclude carrier/handset buffering; measure at the receiving
handset to obtain audible end-to-end latency.

## Runtime control

Services are left running for further local tests. Stop this project's services
to release their inference memory while retaining downloaded models and state:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Action Stop -Profile gpu -AllowLowVram
```

Use `-Action Start` with the same profile and trial switch to start again.
The Docker image now pins pip 25.3 with resumable downloads, following an
initial truncated PyTorch wheel that correctly failed its upstream hash check.
Package versions, model pins, TLS validation and model checksums are retained.
