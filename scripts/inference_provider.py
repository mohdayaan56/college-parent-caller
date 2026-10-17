"""Explicit CPU/CUDA selection; fail GPU startup if kernels never use CUDA."""
import json
import os
from pathlib import Path


def configure_session(ort, options, device):
    if device not in {'cpu', 'cuda'}:
        raise ValueError('Inference device must be cpu or cuda')
    if device == 'cpu':
        return ['CPUExecutionProvider']
    # PyTorch and ORT in the deployment image share CUDA 12.8 / cuDNN 9.
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; check Docker GPU support and NVIDIA driver')
    if hasattr(ort, 'preload_dlls'):
        ort.preload_dlls()
    if 'CUDAExecutionProvider' not in ort.get_available_providers():
        raise RuntimeError('Install the deployment GPU image with onnxruntime-gpu')
    options.enable_profiling = True
    options.profile_file_prefix = '/tmp/college-ort' if os.name != 'nt' else 'college-ort'
    return [('CUDAExecutionProvider', {
        'device_id': 0, 'arena_extend_strategy': 'kSameAsRequested',
        'cudnn_conv_algo_search': 'HEURISTIC',
        'cudnn_conv_use_max_workspace': '0',
    }), 'CPUExecutionProvider']


def verify_cuda_execution(session):
    """Run after the first inference, then dispose of the diagnostic trace."""
    if 'CUDAExecutionProvider' not in session.get_providers():
        raise RuntimeError('CUDA session initialization fell back to CPU')
    trace = Path(session.end_profiling())
    try:
        events = json.loads(trace.read_text(encoding='utf-8'))
        if not any(event.get('args', {}).get('provider') == 'CUDAExecutionProvider'
                   for event in events):
            raise RuntimeError('Inference completed without CUDA kernels; GPU setup is not verified')
    finally:
        trace.unlink(missing_ok=True)
