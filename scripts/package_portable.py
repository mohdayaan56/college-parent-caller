"""Build the portable source release using an explicit public-file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = ('PORTABLE_README.md', 'SETUP_GUIDE.md', 'HANDOVER_VALIDATION.md',
              'setup.ps1', 'setup.sh', '.env.example', '.gitignore', '.dockerignore')
SCRIPTS = ('portable_setup.py', 'inference_provider.py', 'prepare_deployment_voices.py',
           'meta_onnx.py', 'meta_stt_service.py', 'vits_service.py', 'vits_dynamic.py',
           'vits_decoder.py', 'speech_edges.py', 'package_portable.py')
TESTS = ('__init__.py', 'support.py', 'test_portable_setup.py', 'test_api_auth.py',
         'test_ari.py', 'test_audio_cache.py', 'test_campaign.py', 'test_conversation.py',
         'test_csv_verify.py', 'test_live_stt.py', 'test_meta_stt.py',
         'test_streaming_audio.py', 'test_stt.py', 'test_optimized_speech.py',
         'test_vits_dynamic.py', 'test_vits_normalize.py')


def release_paths():
    paths = [ROOT / name for name in ROOT_FILES]
    paths += sorted(path for path in (ROOT / 'app').rglob('*.py')
                    if '__pycache__' not in path.parts)
    paths += [ROOT / 'scripts' / name for name in SCRIPTS]
    paths += [ROOT / 'tests' / name for name in TESTS]
    paths += [ROOT / name for name in ('tests/fixtures/students.csv',
        'data/students.example.csv', 'asterisk/Dockerfile', 'deploy/Dockerfile',
        'deploy/requirements.txt', 'deploy/models.lock.json',
        'deploy/sip-provider.example.conf', 'deploy/ca/.gitkeep')]
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError('Missing or linked release file: ' + str(path))
    return sorted(set(paths))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'College-Parent-Caller-2026-10-08.zip')
    args = parser.parse_args()
    paths = release_paths()
    manifest = {str(path.relative_to(ROOT).as_posix()): {
        'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in paths}
    with ZipFile(args.output, 'w', ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, 'Cursor-MajorProject/' + path.relative_to(ROOT).as_posix())
        archive.writestr('Cursor-MajorProject/RELEASE_MANIFEST.json', json.dumps(manifest, indent=2))
    checksum = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix('.zip.sha256').write_text(checksum + '  ' + args.output.name + '\n')
    with ZipFile(args.output) as archive:
        if archive.testzip():
            raise RuntimeError('Archive CRC verification failed')
        for name in archive.namelist():
            relative = name.partition('/')[2]
            if relative == '.env' or relative.startswith(('runtime/', 'recordings/', '.venv/', 'models/')):
                raise RuntimeError('Private/large directory accidentally entered release')
            if name.endswith(('.pt', '.gguf', '.onnx', '.pem', '.crt', '.sqlite3')):
                raise RuntimeError('Private/model asset accidentally entered release')
    print(f'Created {args.output}: {args.output.stat().st_size:,} bytes, {len(paths)+1} files')
    print('SHA256: ' + checksum)


if __name__ == '__main__':
    main()
