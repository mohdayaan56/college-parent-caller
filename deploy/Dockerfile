FROM python:3.11-slim-bookworm AS base
COPY deploy/ca/ /usr/local/share/ca-certificates/
RUN update-ca-certificates && apt-get update && apt-get install -y --no-install-recommends libsndfile1 ca-certificates curl && rm -rf /var/lib/apt/lists/*
# pip 24 can hash a truncated wheel instead of resuming it. Preserve upstream
# hashes/TLS checks while retrying interrupted large CUDA and PyTorch downloads.
RUN python -m pip install --no-cache-dir --timeout 120 pip==25.3
COPY deploy/requirements.txt /tmp/requirements.txt
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1 \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PIP_TIMEOUT=120 PIP_RESUME_RETRIES=10
WORKDIR /workspace

FROM base AS cpu
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r /tmp/requirements.txt onnxruntime==1.27.0

FROM base AS gpu
# ORT 1.27 defaults to CUDA 13. Use the CUDA 12.8 build line explicitly.
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 && pip install --no-cache-dir -r /tmp/requirements.txt onnxruntime-gpu==1.26.0
