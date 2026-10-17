#!/bin/sh
# Run in CollegePhoneGateway before attaching the shared Bluetooth adapter.
set -eu
expected=6.6.87.2-microsoft-standard-WSL2
test "$(uname -r)" = "$expected" || { echo 'Kernel changed; rebuild matching drivers first.' >&2; exit 1; }
python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('/opt/college-phone-build')
report = json.loads((root/'driver-build.json').read_text())
if report.get('module_layout_crc') != '0xb08e71bf':
    raise SystemExit('Driver metadata lacks the independently verified module-layout ABI')
for name in ('btmtk','btusb'):
    path = root/'mediatek'/(name+'.ko')
    if hashlib.sha256(path.read_bytes()).hexdigest() != report['modules'][name]['sha256']:
        raise SystemExit('Driver checksum changed: '+name)
PY
mkdir -p /mnt/wsl/college-phone-firmware/mediatek
firmware=/lib/firmware/mediatek/BT_RAM_CODE_MT7922_1_1_hdr.bin
if ! test -f "$firmware"; then
    zstd -d "$firmware.zst" -o "$firmware"
fi
cp "$firmware" /mnt/wsl/college-phone-firmware/mediatek/
echo /mnt/wsl/college-phone-firmware > /sys/module/firmware_class/parameters/path
modprobe usbcore
modprobe bluetooth
modprobe rfcomm
modprobe btintel
modprobe btbcm
modprobe btrtl
for module in btmtk btusb; do
    path=/opt/college-phone-build/mediatek/$module.ko
    if test -d /sys/module/$module; then
        loaded=$(cat /sys/module/$module/srcversion)
        expected_source=$(modinfo -F srcversion "$path")
        test "$loaded" = "$expected_source" || { echo "A different $module is loaded; stop calls before replacing it." >&2; exit 1; }
    else
        insmod "$path"
    fi
done
mkdir -p /run/dbus /var/run/asterisk /var/cache/asterisk
if ! test -S /run/dbus/system_bus_socket; then
    dbus-daemon --system --fork
fi
echo 'Bluetooth core, MediaTek driver and firmware prepared. Attach USB, then start BlueZ and Asterisk.'
