"""Build the phone gateway's HFP correction against matching Ubuntu headers.

The default only stages a module. --install requires an idle native Asterisk,
preserves the packaged module, and does not restart Asterisk or dial a number.
"""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

BUILD = Path('/opt/college-phone-mobile')


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Packaged source differs at patch anchor: ' + old[:70])
    return text.replace(old, new, 1)


LISTENER = r'''
/* BlueZ owns the HFP profile and passes its authorized RFCOMM descriptor.
 * A root-only Unix socket bridges it into the existing Asterisk HFP monitor.
 */
static int phone_rfcomm_accept(int *id, int fd, short events, void *data)
{
    struct adapter_pvt *adapter = data;
    struct sockaddr_rc addr, local;
    socklen_t len;
    struct mbl_pvt *pvt;
    struct ucred cred;
    struct timeval timeout = { .tv_sec = 1 };
    char byte = 0, answer = 0;
    char control[CMSG_SPACE(sizeof(int) * 8)];
    struct iovec iov = { .iov_base = &byte, .iov_len = 1 };
    struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control, .msg_controllen = sizeof(control) };
    struct cmsghdr *cmsg;
    int sock = -1, count = 0, bridge = accept(fd, NULL, NULL);
    if (bridge < 0) {
        return 1;
    }
    len = sizeof(cred);
    if (getsockopt(bridge, SOL_SOCKET, SO_PEERCRED, &cred, &len) < 0 || cred.uid != 0)
        goto done;
    setsockopt(bridge, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    if (recvmsg(bridge, &msg, MSG_CMSG_CLOEXEC) != 1)
        goto done;
    for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS
            && cmsg->cmsg_len >= CMSG_LEN(sizeof(int))) {
            size_t n = (cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int), i;
            int *fds = (int *)CMSG_DATA(cmsg);
            for (i = 0; i < n; ++i) {
                if (++count == 1) sock = fds[i]; else close(fds[i]);
            }
        }
    }
    if (count != 1 || byte != 'H' || (msg.msg_flags & (MSG_CTRUNC | MSG_TRUNC)))
        goto done;
    memset(&addr, 0, sizeof(addr));
    memset(&local, 0, sizeof(local));
    len = sizeof(addr);
    if (getpeername(sock, (struct sockaddr *)&addr, &len) < 0
        || addr.rc_family != AF_BLUETOOTH || len != sizeof(addr)) goto done;
    len = sizeof(local);
    if (getsockname(sock, (struct sockaddr *)&local, &len) < 0
        || local.rc_family != AF_BLUETOOTH || len != sizeof(local)
        || bacmp(&local.rc_bdaddr, &adapter->addr)) goto done;
    AST_RWLIST_RDLOCK(&devices);
    AST_RWLIST_TRAVERSE(&devices, pvt, entry) {
        if (pvt->adapter == adapter && pvt->type == MBL_TYPE_PHONE
            && !bacmp(&pvt->addr, &addr.rc_bdaddr)) {
            break;
        }
    }
    if (!pvt) {
        AST_RWLIST_UNLOCK(&devices);
        goto done;
    }
    ast_mutex_lock(&pvt->lock);
    if (adapter->inuse || pvt->connected || pvt->owner) {
        /* Keep the existing connection; reject the duplicate. */
    } else {
        pvt->rfcomm_socket = sock;
        if (start_monitor(pvt)) {
            pvt->connected = 1;
            adapter->inuse = 1;
            answer = 1;
            sock = -1; /* monitor owns the received descriptor */
            ast_verb(3, "BlueZ HFP connection for %s\n", pvt->id);
        } else {
            pvt->rfcomm_socket = -1;
        }
    }
    ast_mutex_unlock(&pvt->lock);
    AST_RWLIST_UNLOCK(&devices);
done:
    if (sock >= 0) close(sock);
    send(bridge, &answer, 1, MSG_NOSIGNAL);
    close(bridge);
    return 1;
}

static int phone_rfcomm_bind(struct adapter_pvt *adapter)
{
    struct sockaddr_un addr;
    mode_t previous_mask;
    int sock = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (sock < 0) {
        return -1;
    }
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    if (snprintf(adapter->phone_bridge_path, sizeof(adapter->phone_bridge_path),
        "/var/run/asterisk/phone-hfp-%s.sock", adapter->id)
        >= sizeof(adapter->phone_bridge_path)) { close(sock); return -1; }
    ast_copy_string(addr.sun_path, adapter->phone_bridge_path, sizeof(addr.sun_path));
    unlink(addr.sun_path);
    previous_mask = umask(0077);
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        umask(previous_mask);
        close(sock);
        return -1;
    }
    umask(previous_mask);
    if (listen(sock, 1) < 0) { close(sock); unlink(addr.sun_path); return -1; }
    adapter->phone_rfcomm_socket = sock;
    return sock;
}
'''


def patched_source(source):
    source = replace_once(source, '#include <signal.h>',
        '#include <signal.h>\n#include <sys/socket.h>\n#include <sys/un.h>\n#include <sys/stat.h>')
    source = replace_once(source, 'int sco_socket;\t\t\t\t\t/*!< sco listener socket */',
        'int phone_rfcomm_socket;\n\tint *phone_rfcomm_id;\n\tchar phone_bridge_path[108];\n\tint sco_socket;\t\t\t\t\t/*!< sco listener socket */')
    source = replace_once(source, 'static int sco_bind(struct adapter_pvt *adapter);',
        'static int sco_bind(struct adapter_pvt *adapter);\nstatic int start_monitor(struct mbl_pvt *pvt);')
    source = replace_once(source, 'static int sco_bind(struct adapter_pvt *adapter)\n{',
        LISTENER + '\nstatic int sco_bind(struct adapter_pvt *adapter)\n{')
    source = replace_once(source, '\tast_copy_string(adapter->id, id, sizeof(adapter->id));',
        '\tadapter->phone_rfcomm_socket = -1;\n\tast_copy_string(adapter->id, id, sizeof(adapter->id));')
    source = replace_once(source, '\t/* start the sco listener for this adapter */', r'''
    /* Accept authorized BlueZ profile connections through the local bridge. */
    if (phone_rfcomm_bind(adapter) < 0) {
        ast_log(LOG_ERROR, "Cannot bind HFP listener for %s\n", adapter->id);
        goto e_remove_sco;
    }
    adapter->phone_rfcomm_id = ast_io_add(adapter->accept_io,
        adapter->phone_rfcomm_socket, phone_rfcomm_accept, AST_IO_IN, adapter);
    if (!adapter->phone_rfcomm_id) {
        close(adapter->phone_rfcomm_socket);
        unlink(adapter->phone_bridge_path);
        goto e_remove_sco;
    }
    /* start the sco listener for this adapter */''')
    source = replace_once(source, '\t\tgoto e_remove_sco;\n\t}\n\n\t/* add the adapter to our global list */',
        '\t\tast_io_remove(adapter->accept_io, adapter->phone_rfcomm_id);\n'
        '\t\tclose(adapter->phone_rfcomm_socket);\n'
        '\t\tunlink(adapter->phone_bridge_path);\n'
        '\t\tgoto e_remove_sco;\n\t}\n\n\t/* add the adapter to our global list */')
    source = replace_once(source, '\t\tclose(adapter->sco_socket);\n\t\tio_context_destroy(adapter->io);',
        '\t\tclose(adapter->phone_rfcomm_socket);\n\t\tunlink(adapter->phone_bridge_path);\n\t\tclose(adapter->sco_socket);\n\t\tio_context_destroy(adapter->io);')
    source = replace_once(source, '\tsdp_session = sdp_register();',
        '\t/* BlueZ ProfileManager owns the HFP SDP record and RFCOMM listener. */\n\tsdp_session = NULL;')
    source = replace_once(source,
        'if (!adapter->inuse && !pvt->connected && !strcmp(adapter->id, pvt->adapter->id)) {',
        'if (pvt->type != MBL_TYPE_PHONE && !adapter->inuse && !pvt->connected && !strcmp(adapter->id, pvt->adapter->id)) {')
    # The profile daemon retains a duplicate for BlueZ disconnection callbacks.
    # Shut down the shared socket when the phone monitor exits, then close ours.
    source = replace_once(source,
        '\tclose(pvt->rfcomm_socket);\n\tclose(pvt->sco_socket);\n\tpvt->sco_socket = -1;\n\n\tmsg_queue_flush(pvt);',
        '\tshutdown(pvt->rfcomm_socket, SHUT_RDWR);\n\tclose(pvt->rfcomm_socket);\n'
        '\tpvt->rfcomm_socket = -1;\n\tclose(pvt->sco_socket);\n\tpvt->sco_socket = -1;\n\n\tmsg_queue_flush(pvt);')
    # This gateway is a hands-free unit. The phone is the audio gateway.
    # SDP and AT+BRSF must describe the same supported features (caller ID only).
    source = replace_once(source, '\tuint32_t service_uuid_int[] = {0, 0, 0, GENERIC_AUDIO_SVCLASS_ID};\n', '')
    source = replace_once(source, 'sdp_uuid128_create(&svc_uuid, &service_uuid_int);',
        'sdp_uuid16_create(&svc_uuid, HANDSFREE_SVCLASS_ID);')
    source = replace_once(source, 'sdp_uuid32_create(&svc_class1_uuid, GENERIC_AUDIO_SVCLASS_ID);',
        'sdp_uuid16_create(&svc_class1_uuid, GENERIC_AUDIO_SVCLASS_ID);')
    source = replace_once(source, 'sdp_uuid32_create(&svc_class2_uuid, HEADSET_PROFILE_ID);',
        'sdp_uuid16_create(&svc_class2_uuid, HANDSFREE_SVCLASS_ID);')
    source = replace_once(source, '\tsdp_set_service_classes(record, svc_uuid_list);', r'''
    sdp_set_service_classes(record, svc_uuid_list);
    {
        sdp_profile_desc_t profile;
        sdp_list_t *profiles;
        sdp_uuid16_create(&profile.uuid, HANDSFREE_PROFILE_ID);
        profile.version = 0x0105;
        profiles = sdp_list_append(NULL, &profile);
        sdp_set_profile_descs(record, profiles);
        sdp_list_free(profiles, NULL);
        sdp_attr_add_new(record, SDP_ATTR_SUPPORTED_FEATURES, SDP_UINT16,
            &(uint16_t){0x0004});
    }''')
    source = replace_once(source, 'const char *service_name = "Asterisk PABX";',
        'const char *service_name = "College Caller Handsfree";')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    package_version = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Version}', 'asterisk'], text=True)
    headers_version = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Version}', 'asterisk-dev'], text=True)
    if package_version != headers_version:
        raise RuntimeError('Asterisk runtime and development headers must match exactly')
    module_paths = subprocess.check_output(['dpkg', '-L', 'asterisk-mobile'], text=True).splitlines()
    module = next(Path(p) for p in module_paths if p.endswith('/chan_mobile.so'))
    backup = BUILD/'chan_mobile.packaged.so'
    stock = backup if backup.exists() else module
    buildopts = Path('/usr/include/asterisk/buildopts.h').read_text()
    checksum = re.search(r'AST_BUILDOPT_SUM "([0-9a-f]+)"', buildopts).group(1)
    if checksum.encode() not in stock.read_bytes():
        raise RuntimeError('Development header build options differ from packaged module')
    candidates = list(BUILD.glob('asterisk-*/addons/chan_mobile.c'))
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one matching Ubuntu source tree')
    dsc = list(BUILD.glob('asterisk_*.dsc'))
    if len(dsc) != 1 or f'Version: {package_version}' not in dsc[0].read_text():
        raise RuntimeError('Downloaded source version differs from installed runtime')
    original = candidates[0].read_text()
    changed = patched_source(original)
    (BUILD/'chan_mobile.hfp.c').write_text(changed)
    output = BUILD/'chan_mobile.hfp.so'
    subprocess.run(['gcc', '-shared', '-fPIC', '-O2', '-g', '-D_GNU_SOURCE',
                    '-DAST_MODULE="chan_mobile"',
                    '-DAST_MODULE_SELF_SYM=__internal_chan_mobile_self',
                    '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-sign-compare',
                    '-o', str(output), str(BUILD/'chan_mobile.hfp.c'),
                    '-lbluetooth', '-lpthread'], check=True)
    if checksum.encode() not in output.read_bytes():
        raise RuntimeError('Compiled module does not preserve the runtime build checksum')
    def undefined(path):
        rows = subprocess.check_output(['nm', '-D', '--undefined-only', str(path)], text=True)
        return {line.split()[-1].split('@')[0] for line in rows.splitlines()}
    added = undefined(output) - undefined(stock)
    allowed = {'sdp_attr_add_new', 'sdp_set_profile_descs', 'recvmsg', 'send',
               'getpeername', 'getsockname', 'getsockopt', 'unlink', 'umask', 'memcmp', 'shutdown'}
    if added - allowed:
        raise RuntimeError('Unexpected additional module dependencies: ' + str(sorted(added)))
    bluetooth = ctypes.CDLL('libbluetooth.so.3')
    libc = ctypes.CDLL(None)
    for symbol in added:
        getattr(bluetooth if symbol.startswith('sdp_') else libc, symbol)
    import difflib
    root = Path(__file__).resolve().parents[1]
    (root/'asterisk/chan_mobile_hfp.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(keepends=True), changed.splitlines(keepends=True),
        fromfile='a/addons/chan_mobile.c', tofile='b/addons/chan_mobile.c')))
    report = {'package_version':package_version, 'build_options_checksum':checksum,
              'original_source_sha256':hashlib.sha256(original.encode()).hexdigest(),
              'module_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
              'module_path':str(module), 'backup_path':str(backup),
              'additional_verified_symbols':sorted(added),
              'connection_owner':'BlueZ ProfileManager1',
              'installed':False, 'live_voice_verified':False}
    if args.install:
        running = subprocess.run(['pgrep', '-x', 'asterisk'], capture_output=True)
        if running.returncode == 0:
            state = subprocess.check_output(['asterisk', '-rx', 'core show channels count'], text=True)
            if not re.search(r'^0 active channels$', state, re.M):
                raise RuntimeError('Stop active calls before installing the staged module')
        elif running.returncode != 1:
            raise RuntimeError('Cannot verify that Asterisk is stopped')
        if not backup.exists():
            shutil.copy2(module, backup)
        staged = module.with_suffix('.hfp-staged')
        shutil.copy2(output, staged)
        staged.chmod(0o755)
        staged.replace(module)
        report['installed'] = True
    (root/'runtime/deployment/bluetooth/mobile-build.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
