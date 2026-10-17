"""Register College Caller as a BlueZ hands-free unit and bridge it to Asterisk.

Run in CollegePhoneGateway after BlueZ and Asterisk. Only the explicitly selected,
already-paired phone can connect. This script never dials or starts pairing.
"""

import argparse
import array
import os
import re
import signal
import socket

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

HF = "0000111e-0000-1000-8000-00805f9b34fb"
AG = "0000111f-0000-1000-8000-00805f9b34fb"
PATH = "/college/handsfree"


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


def handoff(fd, bridge_path):
    """Send one descriptor; Asterisk must explicitly acknowledge ownership."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bridge:
        bridge.settimeout(2)
        bridge.connect(bridge_path)
        bridge.sendmsg([b"H"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                array.array("i", [fd]))])
        if bridge.recv(1) != b"\x01":
            raise Rejected("Asterisk rejected the Bluetooth descriptor")


class Handsfree(dbus.service.Object):
    def __init__(self, bus, phone, adapter, bridge, loop):
        super().__init__(bus, PATH)
        self.bus, self.phone, self.bridge, self.loop = bus, phone, bridge, loop
        self.device = f"/org/bluez/{adapter}/dev_{phone.replace(':', '_')}"
        self.connections = {}
        self.pending = False
        self.last_status = None

    def status(self, message):
        if message != self.last_status:
            print(message, flush=True)
            self.last_status = message

    def properties(self, device):
        return dbus.Interface(self.bus.get_object("org.bluez", device),
                              "org.freedesktop.DBus.Properties")

    def check_phone(self, device):
        if str(device) != self.device:
            raise Rejected("Only the selected phone is permitted")
        props = self.properties(device)
        if str(props.Get("org.bluez.Device1", "Address")).upper() != self.phone:
            raise Rejected("Phone identity mismatch")
        if not props.Get("org.bluez.Device1", "Paired"):
            raise Rejected("Complete authenticated pairing first")

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device, descriptor, properties):
        fd = descriptor.take()
        connection = socket.socket(fileno=fd)
        try:
            self.check_phone(device)
            if self.connections:
                raise Rejected("A hands-free connection is already active")
            handoff(fd, self.bridge)
            watch = GLib.io_add_watch(fd, GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL,
                                     self.lost, str(device))
            self.connections[str(device)] = (connection, watch)
            self.status("Selected phone HFP connection accepted by Asterisk")
        except Exception as exc:
            connection.close()
            self.status(f"HFP handoff failed: {exc}")
            raise Rejected(str(exc)) from exc

    def lost(self, fd, condition, device):
        entry = self.connections.pop(device, None)
        if entry:
            entry[0].close()
        self.status("Phone HFP disconnected; waiting to reconnect")
        return False

    def disconnect(self, device):
        entry = self.connections.pop(str(device), None)
        if entry:
            connection, watch = entry
            GLib.source_remove(watch)
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device):
        self.disconnect(device)

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        self.loop.quit()

    def connect(self):
        if self.connections or self.pending:
            return True
        try:
            self.check_phone(self.device)
            # Trust is scoped to the authenticated, explicitly selected SIM phone.
            # No discoverability, unsolicited pairing, or unrelated-device access.
            self.properties(self.device).Set("org.bluez.Device1", "Trusted",
                                              dbus.Boolean(True))
            device = dbus.Interface(self.bus.get_object("org.bluez", self.device),
                                    "org.bluez.Device1")
            self.pending = True
            self.status("Connecting selected phone's hands-free service")
            device.ConnectProfile(AG, reply_handler=self.connected,
                                  error_handler=self.failed, timeout=15)
        except (dbus.DBusException, Rejected) as exc:
            self.pending = False
            self.status(f"Waiting for selected paired phone: {exc}")
        return True

    def connected(self):
        self.pending = False

    def failed(self, error):
        self.pending = False
        self.status(f"HFP connection pending: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--adapter", default="hci0")
    parser.add_argument("--bridge", default="/var/run/asterisk/phone-hfp-simadapter.sock")
    args = parser.parse_args()
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", args.phone):
        parser.error("phone must be a Bluetooth MAC address")
    if not re.fullmatch(r"hci[0-9]+", args.adapter):
        parser.error("adapter must be an hci adapter name")
    if os.geteuid() != 0:
        parser.error("the root-only Asterisk bridge requires gateway root")
    DBusGMainLoop(set_as_default=True)
    bus, loop = dbus.SystemBus(), GLib.MainLoop()
    profile = Handsfree(bus, args.phone.upper(), args.adapter, args.bridge, loop)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.ProfileManager1")
    # Omit Role: HFP's distinct UUID already identifies our hands-free role,
    # and BlueZ must enable both incoming and outgoing RFCOMM connections.
    manager.RegisterProfile(PATH, HF, {
        "Name": "College Caller Handsfree", "Channel": dbus.UInt16(1),
        "Version": dbus.UInt16(0x0105), "Features": dbus.UInt16(4),
        "RequireAuthentication": dbus.Boolean(True),
        "RequireAuthorization": dbus.Boolean(True),
        "AutoConnect": dbus.Boolean(False),
    })
    print("BlueZ Handsfree profile registered: version 1.5, features 4, channel 1",
          flush=True)
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum,
                             lambda: (loop.quit(), False)[1])
    GLib.idle_add(lambda: (profile.connect(), False)[1])
    GLib.timeout_add_seconds(15, profile.connect)
    try:
        loop.run()
    finally:
        for device in list(profile.connections):
            profile.disconnect(device)
        try:
            manager.UnregisterProfile(PATH)
        except dbus.DBusException:
            pass


if __name__ == "__main__":
    main()
