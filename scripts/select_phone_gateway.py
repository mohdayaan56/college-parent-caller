"""Route the demo caller to the native Bluetooth PBX, with local RTP forwarding."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import re

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'runtime/deployment'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, help='Authorized demo receiver in E.164 format')
    parser.add_argument('--sim-number', required=True, help='Calling phone SIM in E.164 format')
    args = parser.parse_args()
    if not all(re.fullmatch(r'\+[1-9][0-9]{7,14}', value) for value in (args.destination, args.sim_number)):
        parser.error('Phone numbers must use E.164 format, including the leading +')
    compose_path = STATE / 'compose.json'
    backup = STATE / 'compose.before-bluetooth.json'
    if not backup.exists():
        shutil.copy2(compose_path, backup)
    compose = json.loads(compose_path.read_text())
    caller = compose['services']['caller']
    override = {'ARI_URL':'http://host.docker.internal:8189/ari',
                'OUTBOUND_CHANNEL_DRIVER':'mobile', 'MOBILE_DEVICE':'simphone',
                'SIP_DIAL_FORMAT':'national', 'STREAMING_AUDIO_ADVERTISE_HOST':'127.0.0.1'}
    caller['environment'].update(override)
    start = int(caller['environment'].get('STREAMING_AUDIO_PORT', '20100'))
    count = int(caller['environment'].get('STREAMING_AUDIO_PORTS', '4'))
    ports = caller.setdefault('ports', [])
    for port in range(start, start+count):
        mapping = f'127.0.0.1:{port}:{port}/udp'
        if mapping not in ports:
            ports.append(mapping)
    compose_path.write_text(json.dumps(compose, indent=2))
    overrides = STATE / 'caller-overrides.json'
    values = json.loads(overrides.read_text()) if overrides.exists() else {}
    values.update(override)
    overrides.write_text(json.dumps(values, indent=2))
    students = STATE / 'students.csv'
    csv_backup = STATE / 'bluetooth/students.before-phone.csv'
    if not csv_backup.exists():
        shutil.copy2(students, csv_backup)
    with students.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields, records = reader.fieldnames, list(reader)
    phone_record = next((row for row in records if row['student_id'] == 'demo-phone-001'), None)
    if phone_record is None:
        row = next(row for row in records if row['student_id'] == 'demo-001').copy()
        row.update(student_id='demo-phone-001', parent_phone=args.destination)
        records.append(row)
    else:
        phone_record['parent_phone'] = args.destination
    with students.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(records)
    (STATE/'bluetooth/call-target.json').write_text(json.dumps({
        'student_id':'demo-phone-001', 'destination':args.destination,
        'expected_sim_number':args.sim_number, 'scope':'One authorized controlled demo call'}, indent=2))
    print('Phone route configured with a fictional demo record; no call placed.')


if __name__ == '__main__':
    main()
