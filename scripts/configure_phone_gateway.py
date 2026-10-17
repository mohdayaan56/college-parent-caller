"""Configure the dedicated native WSL PBX; does not dial or switch the caller."""
import argparse
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'runtime/deployment'


def address(value):
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', value):
        raise ValueError('A Bluetooth MAC address is required')
    return value.upper()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', required=True, type=address)
    parser.add_argument('--phone', type=address)
    parser.add_argument('--rfcomm-port', type=int)
    args = parser.parse_args()
    if args.phone and (args.rfcomm_port is None or not 1 <= args.rfcomm_port <= 30):
        raise ValueError('Specify the discovered hands-free Audio Gateway RFCOMM port')
    config = Path('/etc/asterisk')
    shutil.copy2(STATE/'asterisk/ari.conf', config/'ari.conf')
    (config/'ari.conf').chmod(0o600)
    (config/'http.conf').write_text('[general]\nenabled=yes\nbindaddr=0.0.0.0\nbindport=8189\n')
    (config/'rtp.conf').write_text('[general]\nrtpstart=10200\nrtpend=10220\n')
    # Inbound SIM calls are never automatically answered by the marks app.
    # Keep the ringing channel passive until the phone/network ends the call.
    (config/'extensions.conf').write_text('[from-phone]\nexten => s,1,Wait(86400)\n')
    mobile = '[general]\ninterval=15\n\n[adapter]\nid=simadapter\naddress='+args.adapter+'\n'
    if args.phone:
        mobile += ('\n[simphone]\naddress='+args.phone+'\nport='+str(args.rfcomm_port)+
                   '\ncontext=from-phone\nadapter=simadapter\ngroup=1\n')
    (config/'mobile.conf').write_text(mobile)
    # Ubuntu's packaged module uses this name rather than upstream mobile.conf.
    (config/'chan_mobile.conf').write_text(mobile)
    source = STATE/'sounds'
    sounds = Path('/usr/share/asterisk/sounds/parent-marks')
    if sounds.exists() and not sounds.is_symlink():
        raise RuntimeError('Refusing to replace an existing native sound directory')
    if not sounds.is_symlink():
        sounds.symlink_to(source, target_is_directory=True)
    report = {'adapter':args.adapter, 'phone':args.phone, 'rfcomm_port':args.rfcomm_port,
              'ari_port':8189, 'device_id':'simphone'}
    (STATE/'bluetooth/gateway.json').write_text(json.dumps(report, indent=2))
    print('Configured native phone gateway on port 8189; no call placed.')


if __name__ == '__main__':
    main()
