"""Check descriptor transfer and rejection without dialing a phone."""

import array
import argparse
import socket
import tempfile
import threading
from pathlib import Path

from phone_hfp_profile import handoff, Rejected


def transfer_check(accept):
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "profile.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            listener.listen(1)
            failures = []

            def receive():
                try:
                    with listener.accept()[0] as bridge:
                        bridge.settimeout(3)
                        data, ancillary, flags, address = bridge.recvmsg(
                            1, socket.CMSG_SPACE(array.array("i").itemsize))
                        assert data == b"H" and not flags
                        assert len(ancillary) == 1
                        level, kind, raw = ancillary[0]
                        assert (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS)
                        fds = array.array("i")
                        fds.frombytes(raw)
                        assert len(fds) == 1
                        with socket.socket(fileno=fds[0]) as transferred:
                            assert transferred.recv(1) == b"V"
                        bridge.sendall(b"\x01" if accept else b"\x00")
                except BaseException as exc:
                    failures.append(exc)

            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            left, right = socket.socketpair()
            with left, right:
                right.sendall(b"V")
                rejected = False
                try:
                    handoff(left.fileno(), path)
                except Rejected:
                    rejected = True
                assert rejected != accept
            thread.join(4)
            assert not thread.is_alive() and not failures, str(failures)


def native_rejection_check(path):
    # A Unix socket must never be admitted as a Bluetooth RFCOMM connection.
    left, right = socket.socketpair()
    with left, right:
        try:
            handoff(left.fileno(), path)
        except Rejected:
            pass
        else:
            raise AssertionError("Asterisk accepted a non-Bluetooth descriptor")
    # A message with no descriptor must also be rejected.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bridge:
        bridge.settimeout(2)
        bridge.connect(path)
        bridge.sendall(b"H")
        assert bridge.recv(1) == b"\x00"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge")
    args = parser.parse_args()
    transfer_check(True)
    transfer_check(False)
    if args.bridge:
        native_rejection_check(args.bridge)
    print("PASS: descriptor transfer, explicit acknowledgement, rejection, native socket validation")
