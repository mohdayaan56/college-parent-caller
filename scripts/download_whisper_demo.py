"""Download a pinned public ASR model with upstream file integrity checks."""
import hashlib
import json
import urllib.request
from scripts import portable_setup as setup

REPO = 'dropbox-dash/faster-whisper-large-v3-turbo'
REVISION = '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'


def download_public_model(repo, revision, wanted, directory):
    metadata = setup.request_json(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true')
    entries = []
    for item in metadata['siblings']:
        name = item['rfilename']
        if name not in wanted:
            continue
        if 'lfs' in item:
            checksum = item['lfs']['sha256']
        else:
            address = f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
            with urllib.request.urlopen(address, context=setup.trust_context(), timeout=90) as response:
                content = response.read()
            git_digest = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
            if git_digest != item['blobId']:
                raise ValueError(f'Upstream Git hash mismatch: {name}')
            checksum = hashlib.sha256(content).hexdigest()
        entries.append({'repo':repo, 'revision':revision, 'file':name,
            'path':f'models/{directory}/{name}', 'bytes':item['size'], 'sha256':checksum})
    if {entry['file'] for entry in entries} != wanted:
        raise ValueError('Incomplete upstream model manifest')
    setup.download_entries(entries)
    (setup.ROOT/'models'/directory/'verified-manifest.json').write_text(json.dumps(entries, indent=2))


def main():
    download_public_model(REPO, REVISION,
        {'config.json','model.bin','preprocessor_config.json','tokenizer.json','vocabulary.json'},
        'whisper-large-v3-turbo')


if __name__ == '__main__':
    main()
