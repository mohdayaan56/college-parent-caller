"""Temporary BlueZ pairing agent restricted to one explicitly selected phone.

Run in CollegePhoneGateway with python3-dbus and python3-gi installed.
"""

import argparse
import re

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


class PhoneAgent(dbus.service.Object):
    def __init__(self, bus, phone, loop):
        super().__init__(bus, "/college/phone_agent")
        self.phone = phone.upper()
        self.bus = bus
        self.loop = loop

    def check(self, device):
        props = dbus.Interface(self.bus.get_object("org.bluez", device),
                               "org.freedesktop.DBus.Properties")
        address = str(props.Get("org.bluez.Device1", "Address")).upper()
        if address != self.phone:
            print(f"Rejected request from unrelated device {address}", flush=True)
            raise Rejected("Only the selected SIM phone may pair")

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        self.check(device)
        print(f"Confirmed selected phone pairing code {int(passkey):06d}", flush=True)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        self.check(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        self.check(device)
        allowed = {f"0000{profile}-0000-1000-8000-00805f9b34fb"
                   for profile in ("1108", "1112", "111e", "111f")}
        if str(uuid).lower() not in allowed:
            raise Rejected("Only headset and hands-free services are authorized")
        print(f"Authorized phone audio service {uuid}", flush=True)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        print("Pairing request canceled", flush=True)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        self.loop.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", args.phone):
        parser.error("phone must be a Bluetooth MAC address")
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    agent = PhoneAgent(bus, args.phone, loop)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    path = "/college/phone_agent"
    manager.RegisterAgent(path, "DisplayYesNo")
    manager.RequestDefaultAgent(path)
    props = dbus.Interface(bus.get_object("org.bluez", "/org/bluez/hci0"),
                           "org.freedesktop.DBus.Properties")
    previous_pairable = props.Get("org.bluez.Adapter1", "Pairable")
    props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
    props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(300))
    props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
    GLib.timeout_add_seconds(300, lambda: (loop.quit(), False)[1])
    print(f"Pairing agent ready for {args.phone.upper()} for five minutes", flush=True)
    try:
        loop.run()
    finally:
        try:
            manager.UnregisterAgent(path)
        except dbus.DBusException:
            pass
        props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
        props.Set("org.bluez.Adapter1", "Pairable", previous_pairable)


if __name__ == "__main__":
    main()
