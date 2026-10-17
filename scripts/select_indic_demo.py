"""Enable native Hindi/Kannada ASR in the installed GPU deployment."""
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    state = root/'runtime/deployment'
    compose = json.loads((state/'compose.json').read_text())
    if not (root/'models/indicconformer-120m-onnx/verified-manifest.json').is_file():
        raise ValueError('Download and verify native ASR assets first')
    backup = state/'compose.meta-before-whisper.json'
    if not backup.exists():
        backup.write_text(json.dumps(compose,indent=2))
    original = json.loads(backup.read_text())
    compose['services']['stt'] = original['services']['stt']
    environment = compose['services']['stt']['environment']
    if environment.get('META_ASR_DEVICE') != 'cuda':
        raise ValueError('This evaluated native ASR setup requires the GPU profile')
    environment.update(META_ASR_BACKEND='indic_hybrid',
        INDIC_ASR_DIR='/workspace/models/indicconformer-120m-onnx')
    (state/'compose.json').write_text(json.dumps(compose,indent=2))
    print('Selected Meta English + IndicConformer Hindi/Kannada on GPU.')


if __name__ == '__main__':
    main()
