"""Export and check the three local VITS decoders; no benchmark or phone call."""
import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch

from scripts.vits_service import load, DIRECTORY


def main():
    torch.set_num_threads(4)
    for language in ('en', 'hi', 'kn'):
        directory = DIRECTORY / (language + '_female')
        source = directory / (language + '_female_vits_30hrs.pt')
        with source.open('rb') as stream:
            source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        output = directory / 'decoder.onnx'
        manifest = directory / 'decoder-provenance.json'
        if output.is_file() and manifest.is_file():
            saved = json.loads(manifest.read_text())
            with output.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if saved['source_sha256'] == source_hash and saved['sha256'] == digest:
                print(language + ': verified existing decoder', flush=True)
                continue
        model, _, _, _ = load(language)
        latent = torch.zeros(1, 192, 32)
        with torch.inference_mode():
            reference = model.exported.waveform_decoder(latent).numpy()
            torch.onnx.export(model.exported.waveform_decoder, (latent,), str(output),
                input_names=['latent'], output_names=['audio'],
                dynamic_axes={'latent': {2: 'frames'}, 'audio': {2: 'samples'}},
                opset_version=17, dynamo=False)
        session = ort.InferenceSession(str(output), providers=['CPUExecutionProvider'])
        actual = session.run(None, {'latent': latent.numpy()})[0]
        if not np.allclose(reference, actual, rtol=1e-3, atol=1e-4):
            raise RuntimeError(language + ': decoder conversion changed the output')
        # Check that export works at a length different from its trace input.
        probe = session.run(None, {'latent': np.zeros((1, 192, 47), np.float32)})[0]
        if probe.shape[-1] != 47 * 256 or not np.isfinite(probe).all():
            raise RuntimeError(language + ': decoder has invalid dynamic output length')
        with output.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        manifest.write_text(json.dumps({'source_sha256': source_hash, 'sha256': digest,
            'torch': torch.__version__, 'onnxruntime': ort.__version__,
            'equivalence': 'rtol=1e-3, atol=1e-4; dynamic length checked'}, indent=2))
        print(language + ': decoder exported and checked', flush=True)


if __name__ == '__main__':
    main()
