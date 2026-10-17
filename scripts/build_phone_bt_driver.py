"""Build only MediaTek USB Bluetooth modules for the exact running WSL kernel.

Run inside CollegePhoneGateway. Sources are the matching Microsoft release.
Dependency symbol CRCs come from that kernel's supplied modules. Missing CRCs
or a different kernel release are fatal; never force-load a mismatched module.
"""
from pathlib import Path
import gzip
import hashlib
import json
import re
import shutil
import struct
import subprocess
import tarfile
import urllib.request

from elftools.elf.elffile import ELFFile

VERSION = '6.6.87.2-microsoft-standard-WSL2'
TAG = 'linux-msft-wsl-6.6.87.2'
ROOT = Path('/opt/college-phone-build')


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def versions(path):
    with path.open('rb') as stream:
        section = ELFFile(stream).get_section_by_name('__versions')
        if section is None:
            return {}
        data = section.data()
        if len(data) % 64:
            raise ValueError('Unexpected module version record layout')
        return {data[offset+8:offset+64].split(b'\0')[0].decode():
                struct.unpack_from('<Q', data, offset)[0]
                for offset in range(0, len(data), 64)}


def exported_versions(path):
    # PREL32 export records contain three 32-bit relative pointers. The name
    # relocation identifies each entry in the corresponding 32-bit CRC table.
    with path.open('rb') as stream:
        elf = ELFFile(stream)
        strings = elf.get_section_by_name('__ksymtab_strings')
        if strings is None:
            return {}
        result = {}
        for suffix in ('', '_gpl'):
            symbols = elf.get_section_by_name('__ksymtab' + suffix)
            checksums = elf.get_section_by_name('__kcrctab' + suffix)
            relocations = elf.get_section_by_name('.rela__ksymtab' + suffix)
            if not symbols or not checksums or not relocations:
                continue
            if symbols.data_size//12 != checksums.data_size//4:
                raise ValueError('Unexpected exported symbol record layout')
            table = elf.get_section(relocations['sh_link'])
            for relocation in relocations.iter_relocations():
                offset = relocation['r_offset']
                if offset % 12 != 4:
                    continue
                symbol = table.get_symbol(relocation['r_info_sym'])
                if not isinstance(symbol['st_shndx'], int) or elf.get_section(symbol['st_shndx']).name != '__ksymtab_strings':
                    raise ValueError('Unexpected exported name relocation')
                position = symbol['st_value'] + relocation['r_addend']
                name = strings.data()[position:].split(b'\0')[0].decode()
                result[name] = struct.unpack_from('<I', checksums.data(), offset//12*4)[0]
        return result


def main():
    release = subprocess.check_output(['uname', '-r'], text=True).strip()
    if release != VERSION:
        raise RuntimeError(f'Expected {VERSION}, found {release}')
    ROOT.mkdir(exist_ok=True)
    archive = ROOT / 'kernel-source.tar.gz'
    if not archive.exists():
        url = f'https://codeload.github.com/microsoft/WSL2-Linux-Kernel/tar.gz/refs/tags/{TAG}'
        with urllib.request.urlopen(url, timeout=180) as response, archive.open('wb') as stream:
            shutil.copyfileobj(response, stream)
    source = ROOT / ('WSL2-Linux-Kernel-' + TAG)
    if not source.exists():
        with tarfile.open(archive) as tar:
            tar.extractall(ROOT, filter='data')
    config = gzip.decompress(Path('/proc/config.gz').read_bytes())
    (source / '.config').write_bytes(config)
    run('make', '-s', 'LOCALVERSION=', 'olddefconfig', cwd=source)
    effective = (source / '.config').read_text()
    for option in ('CONFIG_DEBUG_INFO_BTF_MODULES', 'CONFIG_MODULE_ALLOW_BTF_MISMATCH',
                   'CONFIG_MODULE_UNLOAD', 'CONFIG_KALLSYMS'):
        original = next((line for line in config.decode().splitlines() if line.startswith(option+'=')), None)
        if original and original not in effective.splitlines():
            raise RuntimeError('Build tools changed module-layout option: ' + option)
    run('make', '-s', '-j2', 'LOCALVERSION=', 'modules_prepare', cwd=source)
    prepared = subprocess.check_output(['make','-s','LOCALVERSION=','kernelrelease'], cwd=source, text=True).strip()
    if prepared != release:
        raise RuntimeError(f'Prepared headers do not match running kernel: {prepared}')
    crc = {}
    for path in (Path('/lib/modules') / release / 'kernel').rglob('*.ko'):
        for name, value in {**versions(path), **exported_versions(path)}.items():
            if name in crc and crc[name] != value:
                raise RuntimeError('Inconsistent supplied symbol version: ' + name)
            crc[name] = value
    if 'module_layout' not in crc:
        raise RuntimeError('No matching supplied kernel module versions')
    # Generate this checksum from the actual type declarations, independently
    # of the dependency table. A matching release string alone is insufficient.
    run('make', '-s', '-j2', 'LOCALVERSION=', 'kernel/module/version.o', cwd=source)
    layout = re.search(r'#SYMVER module_layout (0x[0-9a-f]+)',
                       (source / 'kernel/module/.version.o.cmd').read_text())
    if not layout or int(layout[1], 16) != crc['module_layout']:
        raise RuntimeError('Compiled module structure ABI differs from the running kernel')
    (source / 'Module.symvers').write_text(''.join(
        f'0x{value:08x}\t{name}\tvmlinux\tEXPORT_SYMBOL\t\n' for name, value in sorted(crc.items())))
    module = ROOT / 'mediatek'
    module.mkdir(exist_ok=True)
    for path in (source / 'drivers/bluetooth').iterdir():
        if path.suffix in {'.c','.h'}:
            shutil.copy2(path, module / path.name)
    (module / 'Makefile').write_text('obj-m += btusb.o btmtk.o\n'
        'ccflags-y += -DCONFIG_BT_HCIBTUSB_MTK=1 -DCONFIG_BT_MTK_MODULE=1\n')
    run('make', '-s', '-j2', '-C', str(source), 'LOCALVERSION=', 'M='+str(module), 'modules')
    own = {line.split()[1] for line in (module / 'Module.symvers').read_text().splitlines()}
    report = {'kernel':release, 'source_tag':TAG,
              'module_layout_crc':layout[1],
              'source_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(), 'modules':{}}
    for name in ('btmtk','btusb'):
        path = module / (name + '.ko')
        vermagic = subprocess.check_output(['modinfo','-F','vermagic',str(path)], text=True).strip()
        if vermagic.split()[0] != release:
            raise RuntimeError('Module version mismatch: ' + name)
        for symbol, value in versions(path).items():
            if symbol not in own and crc.get(symbol) != value:
                raise RuntimeError('Dependency ABI mismatch: ' + symbol)
        report['modules'][name] = {'vermagic':vermagic, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (ROOT / 'driver-build.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
