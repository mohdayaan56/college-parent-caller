"""Select the verified local Whisper English model, keeping native Indic ASR."""

import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    state = root/'runtime/deployment'
    if not (root/'models/whisper-large-v3-turbo/verified-manifest.json').is_file():
        raise RuntimeError('Verified local Whisper assets missing')
    path = state/'compose.json'
    original = path.read_text()
    backup = state/'bluetooth/compose.before-phone-recognition.json'
    if not backup.exists():
        backup.write_text(original)
    compose = json.loads(original)
    service = compose['services']['stt']
    if service['environment'].get('META_ASR_BACKEND') != 'indic_hybrid':
        raise RuntimeError('Expected the existing native Hindi/Kannada deployment')
    service['image'] = 'college-parent-speech:whisper'
    service['build'] = {'context':str(root),'dockerfile':'deploy/Dockerfile.whisper'}
    service['environment'].update(
        ENGLISH_ASR_BACKEND='whisper',
        WHISPER_MODEL_DIR='/workspace/models/whisper-large-v3-turbo',
        LD_LIBRARY_PATH='/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:'
                        '/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:'
                        '/usr/local/lib/python3.11/site-packages/nvidia/cuda_runtime/lib')
    path.write_text(json.dumps(compose,indent=2))
    print('Selected local Whisper English + native Hindi/Kannada; saved rollback config')


if __name__ == '__main__':
    main()
