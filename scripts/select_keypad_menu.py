"""Enable the multilingual keypad menu in the current local demo deployment."""
import json
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics',action='store_true',help='Capture only bounded menu audio locally')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1]/'runtime/deployment/compose.json'
    config = json.loads(path.read_text())
    config['services']['caller']['environment']['LANGUAGE_MENU_KEYPAD_ONLY'] = 'true'
    config['services']['caller']['environment']['PHONE_MENU_DIAGNOSTICS'] = str(args.diagnostics).lower()
    path.write_text(json.dumps(config,indent=2))
    print('Enabled keypad-only menu; other deployment settings preserved')


if __name__ == '__main__':
    main()
