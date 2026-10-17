"""Optional ONNX waveform decoder, using the original VITS frontend and weights."""
from pathlib import Path
import os

_sessions = {}
_verified = set()


def status():
    return {'device': os.environ.get('VITS_DECODER_DEVICE', 'cpu'),
            'cuda_verified': bool(_verified)}


def decode(latent, directory: Path, threads: int):
    import onnxruntime as ort
    path = directory/'decoder.onnx'
    if not path.is_file():
        raise RuntimeError('Export the local decoder with scripts.benchmark_vits_runtime first')
    device = os.environ.get('VITS_DECODER_DEVICE', 'cpu')
    key = (str(path.resolve()), path.stat().st_mtime_ns, threads, device)
    if key not in _sessions:
        _sessions.clear()  # match the one-voice residency policy
        _verified.clear()
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        from scripts.inference_provider import configure_session
        _sessions[key] = ort.InferenceSession(str(path), sess_options=options,
                                              providers=configure_session(ort, options, device))
        _sessions[key].disable_fallback()
    result = _sessions[key].run(['audio'], {'latent': latent.cpu().numpy()})[0].reshape(-1)
    if device == 'cuda' and key not in _verified:
        from scripts.inference_provider import verify_cuda_execution
        verify_cuda_execution(_sessions[key])
        _verified.add(key)
    return result
