"""Select the installed alternative ASR; retain the original Compose configuration."""
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    state = root/'runtime/deployment'
    compose_path = state/'compose.json'
    compose = json.loads(compose_path.read_text())
    manifest = root/'models/whisper-large-v3-turbo/verified-manifest.json'
    if not manifest.is_file():
        raise ValueError('Run scripts.download_whisper_demo first')
    backup = state/'compose.meta-before-whisper.json'
    if not backup.exists():
        backup.write_text(json.dumps(compose,indent=2))
    service = compose['services']['stt']
    service['build'] = {'context':service['build']['context'], 'dockerfile':'deploy/Dockerfile.whisper'}
    service['image'] = 'college-parent-speech:whisper'
    service['environment'].update(META_ASR_BACKEND='faster_whisper',
        WHISPER_MODEL_DIR='/workspace/models/whisper-large-v3-turbo',
        WHISPER_DEVICE='cuda', WHISPER_COMPUTE_TYPE='int8_float16')
    service['environment']['LD_LIBRARY_PATH'] = ':'.join(
        '/usr/local/lib/python3.11/site-packages/nvidia/'+name+'/lib'
        for name in ('cudnn','cublas','cuda_runtime'))
    compose_path.write_text(json.dumps(compose,indent=2))
    print('Selected local Whisper large-v3-turbo; original configuration retained.')


if __name__ == '__main__':
    main()
