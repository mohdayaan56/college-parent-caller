"""Select the existing loopback-only SIP demo; never place a call."""
import configparser
import csv
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'runtime/deployment'


def configure(state=STATE):
    compose_path = state / 'compose.json'
    overrides_path = state / 'caller-overrides.json'
    backup = state / 'local-sip'
    backup.mkdir(exist_ok=True)
    for path in (compose_path, overrides_path):
        saved = backup / (path.name + '.before-local-sip')
        if path.exists() and not saved.exists():
            shutil.copy2(path, saved)
            saved.chmod(0o600)

    compose = json.loads(compose_path.read_text())
    credentials = json.loads((state / 'credentials.json').read_text())
    display_name = 'Local demo parent'
    dataset = state / 'students.csv'
    if dataset.is_file():
        with dataset.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if row.get('student_id') == 'demo-001':
                    display_name = row.get('parent_name', '').strip() or display_name
                    break
    values = {'ARI_URL': 'http://pbx:8088/ari', 'ARI_USER': 'college',
              'ARI_PASSWORD': credentials['ari_password'],
              'OUTBOUND_CHANNEL_DRIVER': 'pjsip', 'SIP_ENDPOINT': 'PJSIP',
              'SIP_TRUNK': 'zoiper1', 'SIP_DIAL_FORMAT': 'e164_digits',
              'STREAMING_AUDIO_ADVERTISE_HOST': 'caller',
              'LANGUAGE_MENU_KEYPAD_ONLY': 'true', 'PHONE_MENU_DIAGNOSTICS': 'false'}
    compose['services']['caller']['environment'].update(values)
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    overrides.update(values)

    pjsip_path = state / 'asterisk/pjsip.conf'
    text = pjsip_path.read_text()
    endpoint = '[zoiper1]\ntype=endpoint\n'
    if text.count(endpoint) != 1:
        raise ValueError('Expected the existing local zoiper1 endpoint')
    if 'dtmf_mode=' not in text.split(endpoint, 1)[1].split('[', 1)[0]:
        text = text.replace(endpoint, endpoint + 'dtmf_mode=rfc4733\n', 1)

    softphone_path = state / 'demo-softphone/MicroSIP.ini'
    softphone = configparser.ConfigParser(interpolation=None, strict=False)
    softphone.optionxform = str
    if softphone_path.exists():
        raw = softphone_path.read_bytes()
        softphone.read_string(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))
    if 'Settings' not in softphone:
        softphone['Settings'] = {}
    softphone['Settings'].update({'accountId': '1', 'enableLocalAccount': '0',
                                 'DTMFMethod': '0', 'autoAnswer': '0', 'AA': '0',
                                 'autoRecording': '0', 'audioCodecs': 'PCMA/8000/1 PCMU/8000/1'})
    softphone['Account1'] = {'label': 'Local college demo', 'server': '127.0.0.1:15070',
                             'domain': '127.0.0.1', 'username': 'zoiper1', 'authID': 'zoiper1',
                             'password': credentials['sip_password'], 'displayName': display_name,
                             'transport': 'udp', 'publish': '0', 'ICE': '0', 'allowRewrite': '1'}
    if softphone_path.exists() and not (backup / 'MicroSIP.ini.before-local-sip').exists():
        shutil.copy2(softphone_path, backup / 'MicroSIP.ini.before-local-sip')
        (backup / 'MicroSIP.ini.before-local-sip').chmod(0o600)
    compose_path.write_text(json.dumps(compose, indent=2))
    overrides_path.write_text(json.dumps(overrides, indent=2))
    overrides_path.chmod(0o600)
    pjsip_path.write_text(text)
    with softphone_path.open('w', encoding='utf-16') as output:
        softphone.write(output, space_around_delimiters=False)
    softphone_path.chmod(0o600)
    print('Selected local SIP extension zoiper1; no mobile call placed. Restart caller and reload PJSIP.')


if __name__ == '__main__':
    configure()
