#!/usr/bin/env python3
"""Verified stock install + portable Wine SDK; no build stamps or target reuse."""
import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile

SCHEMA = 'proton-stock-closure-1'
ROOTS = {'dist', 'zluda-wine-sdk', 'src-wine', 'dst-wine-i386', 'dst-wine-x86_64'}
# Candidate assembly always writes these; they are not stock install artifacts.
ASSEMBLY = {'version', 'LICENSE'}
LIMIT = 8 * 1024**3


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def write(path, value):
    Path(path).write_bytes(encoded(value))


def command(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def identity(source, build):
    source, build = source.resolve(), build.resolve()
    sdk = os.environ['SDK']
    if '@sha256:' not in sdk:
        raise ValueError('SDK must be digest pinned')
    # Only the native/backend and bridge gitlinks are excluded. Every other
    # tracked root input (including recipes and patches) participates.
    entries = command('git', 'ls-tree', '-r', 'HEAD', cwd=source).splitlines()
    root = [line for line in entries if line.split('\t', 1)[1] not in ('zluda', 'zluda-wine')]
    if command('git', 'diff', '--ignore-submodules=all', 'HEAD', '--', cwd=source):
        raise ValueError('Root tracked source differs from committed recipe')
    modules = {}
    for line in command('git', 'submodule', 'status', '--recursive', cwd=source).splitlines():
        fields = line.split()
        if len(fields) < 2 or line[0] in '-+U':
            raise ValueError('Uninitialized or mismatched submodule: ' + line)
        pin, name = fields[:2]
        if name.split('/')[0] in {'zluda', 'zluda-wine'}:
            continue
        directory = source / name
        # The reviewed Piper prefetch deliberately edits its tracked CMake file.
        # Bind actual patch bytes, rather than treating that edit as clean HEAD.
        delta = subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=directory)
        if command('git', 'ls-files', '--others', '--exclude-standard', cwd=directory):
            raise ValueError('Untracked stock source inputs: ' + name)
        modules[name] = {'pin': pin, 'describe': command('git', 'describe', '--always', '--dirty', cwd=directory),
                         'tracked_delta_sha256': hashlib.sha256(delta).hexdigest()}
    tools = {}
    ccache = Path(shutil.which('ccache') or '/missing-ccache').resolve(strict=True)
    for name in ('cc', 'c++', 'ld', 'objcopy', 'strip', 'cmake', 'ninja', 'ccache',
                 'x86_64-w64-mingw32-gcc', 'i686-w64-mingw32-gcc'):
        choices = [Path(directory) / name for directory in os.environ['PATH'].split(os.pathsep)]
        choices = [p.resolve() for p in choices if p.is_file() and os.access(p, os.X_OK)]
        # SteamRT puts ccache masquerade symlinks before the actual compilers.
        # Bind the underlying compiler binary, not merely the wrapper again.
        path = next((p for p in choices if name == 'ccache' or p != ccache), None)
        if path is None:
            raise ValueError('Missing actual tool: ' + name)
        tools[name] = {'path': str(path), 'sha256': sha(path)}
    inputs = {'schema': SCHEMA, 'root_entries': root, 'stock_modules': modules,
              'SDK': sdk, 'tools': tools,
              'packages': command('dpkg-query', '-W', '-f=${Package}\t${Version}\n'),
              'source_path': str(source), 'build_path': str(build),
              'configuration': {'target': 'x86_64', 'jobs': 3, 'base_epoch': 1791559305,
                                'source_epoch': 1791559305, 'unstripped': False},
              'environment': {k: os.environ.get(k) for k in
                              ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'RUSTFLAGS')}}
    return {'inputs': inputs, 'digest': hashlib.sha256(encoded(inputs)).hexdigest()}


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or str(p) != name or any(x in ('.', '..') for x in p.parts):
        raise ValueError('Unsafe path: ' + name)
    return p


def inside_link(name, target):
    p = safe_name(name)
    if not target or target.startswith('/') or '\\' in target:
        raise ValueError('Unsafe link: ' + name)
    parts = list(p.parts[:-1])
    for part in PurePosixPath(target).parts:
        if part == '..':
            if len(parts) <= 1:
                raise ValueError('Link leaves component root: ' + name)
            parts.pop()
        elif part != '.':
            parts.append(part)
    if not parts or parts[0] != p.parts[0]:
        raise ValueError('Link crosses component root: ' + name)


def tree(root, strict_links=False):
    result = {}
    def visit(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            safe_name(name)
            info = path.lstat()
            row = {'mode': stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                row.update(type='link', target=os.readlink(path))
                if strict_links:
                    inside_link(name, row['target'])
            elif stat.S_ISDIR(info.st_mode):
                row['type'] = 'dir'
            elif stat.S_ISREG(info.st_mode):
                with path.open('rb') as stream:
                    magic = stream.read(4)
                row.update(type='file', sha256=sha(path), size=info.st_size,
                           binary=bool(info.st_mode & 0o111) or magic.startswith(b'MZ') or magic == b'\x7fELF')
            else:
                raise ValueError('Special file: ' + name)
            result[name] = row
            if row['type'] == 'dir':
                visit(path)
    visit(root)
    return result


def copy_checked(source, target, allowed):
    """Dereference SDK symlinks only within this build; never copy special files."""
    resolved = source.resolve(strict=True)
    if not any(resolved.is_relative_to(a.resolve()) for a in allowed):
        raise ValueError('SDK link escapes approved build inputs: ' + str(source))
    info = resolved.stat()
    if stat.S_ISDIR(info.st_mode):
        target.mkdir()
        for child in sorted(resolved.iterdir()):
            copy_checked(child, target / child.name, allowed)
        target.chmod(stat.S_IMODE(info.st_mode))
    elif stat.S_ISREG(info.st_mode):
        shutil.copyfile(resolved, target)
        target.chmod(stat.S_IMODE(info.st_mode))
    else:
        raise ValueError('Special SDK input')


def validate_sdk(source, build):
    sdk = build / 'zluda-wine-sdk'
    contract = json.loads((source / 'make/zluda-wine-abi.json').read_text())
    receipt = json.loads((sdk / 'ABI-RECEIPT.json').read_text())
    if receipt['contract'] != 'zluda-wine-prepared-abi-2' or receipt['wine_pin'] != contract['wine_pin']:
        raise ValueError('SDK ABI identity mismatch')
    flags = {'callback_id_zero', 'same_thread_destructor', 'process_exit', 'thread_exit',
             'loader_lock_refusal', 'locally_scoped_strong_provider', 'eager_binding'}
    if (receipt['prepared_files'] != contract['prepared_files'] or
            receipt['verified'] != dict.fromkeys(flags, True) or
            set(receipt['sdk_files']) != {'bin/winebuild', 'lib/ntdll.so'}):
        raise ValueError('SDK ABI source/flags mismatch')
    for name, expected in contract['prepared_files'].items():
        if sha(build / 'src-wine' / name) != expected:
            raise ValueError('Prepared Wine ABI source mismatch: ' + name)
    for name, expected in receipt['sdk_files'].items():
        if sha(sdk / name) != expected:
            raise ValueError('SDK binary mismatch: ' + name)
    for name in ('include/windef.h', 'include/wine/server_protocol.h', 'include-generated/wtypes.h'):
        if not (sdk / name).is_file():
            raise ValueError('Incomplete portable SDK: ' + name)


def validate_runtime(build):
    path = build / 'dist/files/share/openxr/wineopenxr64.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing installed runtime manifest: files/share/openxr/wineopenxr64.json')


def export(source, build, output):
    if output.exists() or not (build / 'dist').is_dir():
        raise ValueError('Export output exists or stock install is absent')
    validate_runtime(build)
    ident = identity(source, build)
    # Extra installed-tree/SDK staging plus compressed export must fit without
    # relying on the runner's original admission floor remaining free.
    estimate = int(command('du', '-sLb', str(build / 'dist')).split()[0])
    estimate += int(command('du', '-sLb', str(build / 'zluda-wine-sdk')).split()[0])
    if shutil.disk_usage(build.parent).free < 2 * estimate + 2 * 1024**3:
        raise ValueError('Insufficient room for stock staging/export')
    contract = json.loads((source / 'make/zluda-wine-abi.json').read_text())
    stub_sources = {'dlls/nvcuda/Makefile.in', 'dlls/nvcuda/nvcuda.spec'}
    with tempfile.TemporaryDirectory(prefix='stock-export-', dir=build.parent) as temporary:
        stage = Path(temporary)
        shutil.copytree(build / 'dist', stage / 'dist', symlinks=True,
                        ignore=lambda d, names: ASSEMBLY if Path(d) == build / 'dist' else ())
        if (stage / 'dist/files/share/default_pfx').exists() or (stage / 'dist/files/lib/wine-cuda').exists():
            raise ValueError('Export must precede prefix, native and bridge assembly')
        copy_checked(build / 'zluda-wine-sdk', stage / 'zluda-wine-sdk', [build])
        for name in sorted(set(contract['prepared_files']) | stub_sources):
            dest = stage / 'src-wine' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build / 'src-wine' / name, dest)
        for arch in ('i386', 'x86_64'):
            name = f'dst-wine-{arch}/lib/wine/{arch}-windows/nvcuda.dll'
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build / name, dest)
        payload = tree(stage, strict_links=True)
        record = {'schema': SCHEMA, 'identity': ident, 'files': payload}
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
            data = encoded(record)
            info = tarfile.TarInfo('MANIFEST.json'); info.size = len(data); info.mode = 0o644
            import io
            archive.addfile(info, io.BytesIO(data))
            for name in payload:
                archive.add(stage / name, arcname=name, recursive=False)
        # Both proof arms consume exactly the portable SDK/source/stub roots.
        # Cold compilation is finished: no extra headers or raw install files
        # from its full compiler trees may survive into the imported interface.
        for name in sorted(ROOTS - {'dist'}):
            shutil.rmtree(build / name)
            shutil.copytree(stage / name, build / name, symlinks=True)
        write(build / 'STOCK-READY.json', record)
        write(build / 'STOCK-EXPORT.json', {'archive_sha256': sha(output), 'bytes': output.stat().st_size,
                                         'identity': ident, 'files': payload})
    validate_sdk(source, build)


def unpack(archive_path, stage):
    """No extractall, links-as-parents, hardlinks, devices or unlisted payload."""
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or sum(m.size for m in members) > LIMIT:
            raise ValueError('Duplicate archive members or byte limit')
        m = archive.getmember('MANIFEST.json')
        if not m.isfile() or m.size > 32 * 1024**2:
            raise ValueError('Invalid stock manifest')
        record = json.load(archive.extractfile(m))
        if record.get('schema') != SCHEMA or set(names) != set(record['files']) | {'MANIFEST.json'}:
            raise ValueError('Manifest/member coverage mismatch')
        rows = record['files']
        for name, row in rows.items():
            p = safe_name(name)
            if p.parts[0] not in ROOTS or any(rows.get(str(parent), {}).get('type') != 'dir'
                                            for parent in p.parents if str(parent) != '.'):
                raise ValueError('Unknown root or non-directory parent: ' + name)
            if row.get('mode', -1) < 0 or row['mode'] > 0o777:
                raise ValueError('Invalid archive mode')
            if row['type'] == 'link':
                inside_link(name, row['target'])
        for name in sorted(rows, key=lambda n: (len(PurePosixPath(n).parts), n)):
            row = rows[name]; member = archive.getmember(name); dest = stage / name
            if row['type'] == 'dir' and member.isdir():
                dest.mkdir(mode=0o755)
            elif row['type'] == 'file' and member.isfile() and member.size == row['size']:
                with dest.open('xb') as stream:
                    shutil.copyfileobj(archive.extractfile(member), stream)
                dest.chmod(row['mode'])
            elif row['type'] == 'link' and member.issym() and member.linkname == row['target']:
                dest.symlink_to(row['target'])
            else:
                raise ValueError('Member type or size mismatch: ' + name)
        for name in sorted(rows, reverse=True):
            if rows[name]['type'] == 'dir':
                (stage / name).chmod(rows[name]['mode'])
        if tree(stage, strict_links=True) != rows:
            raise ValueError('Extracted stock bytes/metadata mismatch')
        return record


def load_stock(source, build, archive, expected):
    if sha(archive) != expected or len(expected) != 64:
        raise ValueError('Pinned stock archive digest mismatch')
    if any(build.iterdir()):
        raise ValueError('Stock import requires a fresh empty build directory')
    with tempfile.TemporaryDirectory(prefix='stock-import-', dir=build.parent) as temporary:
        stage = Path(temporary)
        record = unpack(archive, stage)
        if record['identity'] != identity(source, build):
            raise ValueError('Exact stock input identity mismatch')
        validate_runtime(stage)
        validate_sdk(source, stage)
        for name in ROOTS:
            if not (stage / name).is_dir():
                raise ValueError('Incomplete stock roots')
        for name in ROOTS:
            (stage / name).rename(build / name)
        write(build / 'STOCK-READY.json', record)
        write(build / 'STOCK-IMPORT.json', {'archive_sha256': expected, 'identity': record['identity'],
                                         'pre_assembly_byte_metadata_equal': True, 'files': record['files']})


def ready(source, build):
    record = json.loads((build / 'STOCK-READY.json').read_text())
    if record['schema'] != SCHEMA or record['identity'] != identity(source, build):
        raise ValueError('Stock import input drift')
    # Candidate assembly deliberately changes dist. The SDK/source/raw stubs
    # must remain exactly the validated stock export throughout its use.
    for name in sorted(ROOTS - {'dist'}):
        path = build / name
        row = record['files'].get(name)
        wanted = {n[len(name) + 1:]: r for n, r in record['files'].items() if n.startswith(name + '/')}
        if (not row or path.is_symlink() or not path.is_dir() or
                stat.S_IMODE(path.stat().st_mode) != row['mode'] or tree(path) != wanted):
            raise ValueError('Imported SDK/source/raw stub bytes/type/mode/coverage drift: ' + name)
    validate_sdk(source, build)
    validate_runtime(build)


def qualification(build, output):
    record = json.loads((build / 'STOCK-READY.json').read_text())
    rows = tree(build / 'redist')
    registries = {n: (build / 'redist' / n).read_text() for n in rows
                  if registry(n) and rows[n]['type'] == 'file'}
    auxiliary = {n: (build / 'redist' / n).read_bytes().hex() for n in rows
                 if (update_timestamp(n) or installer_msi(n)) and rows[n]['type'] == 'file'}
    write(output, {'schema': SCHEMA, 'stock_files': record['files'],
                   'stock_identity': record['identity'], 'installed_files': rows,
                   'registry_text': registries,
                   'wineboot_auxiliary_hex': auxiliary,
                   'version_text': (build / 'redist/version').read_text()})


def registry(name):
    p = PurePosixPath(name)
    return str(p.parent) in ('files/share/default_pfx', 'files/share/default_pfx_arm64') and p.name in (
        'user.reg', 'system.reg', 'userdef.reg')


def update_timestamp(name):
    return name in ('files/share/default_pfx/.update-timestamp',
                    'files/share/default_pfx_arm64/.update-timestamp')


def installer_msi(name):
    return bool(re.fullmatch(r'files/share/default_pfx(?:_arm64)?/drive_c/windows/Installer/[0-9a-fA-F]{4}\.msi', name))


def raw_auxiliary(row, name):
    data = bytes.fromhex(row['wineboot_auxiliary_hex'][name])
    wanted = row['installed_files'][name]
    if len(data) != wanted['size'] or hashlib.sha256(data).hexdigest() != wanted['sha256']:
        raise ValueError('Wineboot raw evidence does not match inventory: ' + name)
    return data


def installer_renames(a, b):
    """Only one equal-content file changing its four-hex cache name per prefix."""
    pairs, lookup = [], {}
    for prefix in ('files/share/default_pfx', 'files/share/default_pfx_arm64'):
        directory = prefix + '/drive_c/windows/Installer'
        def names(row):
            return [n for n in row['installed_files'] if str(PurePosixPath(n).parent) == directory
                    and PurePosixPath(n).suffix.lower() == '.msi']
        old, new = names(a), names(b)
        if len(old) != 1 or len(new) != 1 or old == new or not all(installer_msi(n) for n in old + new):
            continue
        x, y = a['installed_files'][old[0]], b['installed_files'][new[0]]
        if (x.get('type') != 'file' or y.get('type') != 'file' or x.get('binary') or y.get('binary') or
                any(x.get(k) != y.get(k) for k in ('sha256', 'size', 'mode'))):
            continue
        if raw_auxiliary(a, old[0]) != raw_auxiliary(b, new[0]):
            raise ValueError('Equal-hash MSI raw evidence differs')
        index = len(pairs)
        pairs.append({'cold_path': old[0], 'warm_path': new[0], 'sha256': x['sha256'],
                      'size': x['size'], 'mode': x['mode'], 'content_hex': a['wineboot_auxiliary_hex'][old[0]],
                      'generator': 'default_pfx.py make_default_pfx -> Wine wineboot installer cache',
                      'classification': 'UNCLASSIFIED random installer cache rename; B review required'})
        lookup[old[0]] = lookup[new[0]] = index
    return pairs, lookup


def compare(cold, warm):
    a, b = json.loads(cold.read_text()), json.loads(warm.read_text())
    differences, forbidden, manual = {}, [], []
    renames, rename_lookup = installer_renames(a, b)
    for name in sorted(set(a['installed_files']) | set(b['installed_files'])):
        x, y = a['installed_files'].get(name), b['installed_files'].get(name)
        if x == y:
            continue
        differences[name] = {'cold': x, 'warm': y}
        if name in rename_lookup:
            differences[name].update(classification='UNCLASSIFIED Wineboot MSI cache rename',
                                     rename_evidence_index=rename_lookup[name])
            manual.append(name)
            continue
        # Binary bytes/type/mode can never be exempted, including prefix stubs.
        if not x or not y or x.get('binary') or y.get('binary') or any(
                x.get(k) != y.get(k) for k in ('type', 'mode', 'target')):
            forbidden.append(name)
        elif name == 'version':
            xa = re.fullmatch(r'([0-9]+) (.+)\n', a['version_text'])
            xb = re.fullmatch(r'([0-9]+) (.+)\n', b['version_text'])
            if not xa or not xb or xa[2] != xb[2]:
                forbidden.append(name)
            else:
                differences[name]['class'] = 'version date +%s stamp (Makefile.in all-dist)'
        elif registry(name):
            differences[name]['raw_diff'] = ''.join(difflib.unified_diff(
                a['registry_text'][name].splitlines(True), b['registry_text'][name].splitlines(True)))
            differences[name]['class'] = 'UNCLASSIFIED Wineboot registry delta; manual review required'
            manual.append(name)
        elif update_timestamp(name) and x['type'] == 'file':
            differences[name]['raw_evidence_hex'] = {'cold': raw_auxiliary(a, name).hex(),
                                                      'warm': raw_auxiliary(b, name).hex()}
            differences[name]['class'] = 'UNCLASSIFIED timestamp (default_pfx.py make_default_pfx -> Wine wineboot); B review required'
            manual.append(name)
        else:
            forbidden.append(name)
    stock_equal = a['stock_files'] == b['stock_files'] and a['stock_identity'] == b['stock_identity']
    return {'schema': SCHEMA, 'stock_and_sdk_byte_metadata_equal': stock_equal,
            'final_binary_equal': not any((v['cold'] or {}).get('binary') or
                                         (v['warm'] or {}).get('binary') for v in differences.values()),
            'forbidden_differences': forbidden, 'registry_requires_manual_classification': manual,
            'wineboot_requires_manual_classification': manual,
            'wineboot_installer_rename_evidence': renames,
            'all_full_tree_differences': differences,
            'equivalence': 'FAIL' if not stock_equal or forbidden else ('NEEDS_REGISTRY_REVIEW' if manual else 'PASS'),
            'registry_review_contract': 'Only registry timestamps/host state, same-type/mode .update-timestamp, and exactly one equal-sha/size/mode four-hex Installer MSI rename per prefix, generated by default_pfx.py make_default_pfx -> Wine wineboot; B must classify every delta, otherwise FAIL. No edits or normalization.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['identity', 'export', 'import', 'ready', 'inventory', 'qualification', 'compare'])
    p.add_argument('paths', type=Path, nargs='+')
    a = p.parse_args(); v = a.paths
    if a.action == 'identity':
        write(v[2], identity(v[0], v[1]))
    elif a.action == 'export':
        export(*v)
    elif a.action == 'import':
        load_stock(v[0], v[1], v[2], str(v[3]))
    elif a.action == 'ready':
        ready(*v)
    elif a.action == 'inventory':
        write(v[1], {'evidence_kind': 'pre-launch inventory, not qualification',
                     'installed_files': tree(v[0] / 'redist')})
    elif a.action == 'qualification':
        qualification(*v)
    elif a.action == 'compare':
        result = compare(v[0], v[1]); write(v[2], result)
        return 1 if result['equivalence'] == 'FAIL' else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
