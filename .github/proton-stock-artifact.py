#!/usr/bin/env python3
"""Read one pinned cold seed; compiler-input compatibility is checked on import."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import zipfile

BRANCH = 'refs/heads/ci/proton-stock-reuse-20261108'
FILES = {'stock.tar.gz', 'COLD-QUALIFICATION.json', 'STOCK-EXPORT.json',
         'STOCK-INPUT-BEFORE.json', 'STOCK-INPUT-AFTER.json'}


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True, timeout=60))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate(env):
    mode = env['STOCK_MODE']
    if mode not in ('off', 'cold', 'warm'):
        raise ValueError('Unknown stock mode')
    seed = [env.get(n, '') for n in ('STOCK_SEED_RUN', 'STOCK_SEED_ARTIFACT', 'STOCK_SEED_DIGEST', 'STOCK_SEED_HEAD')]
    if mode == 'off':
        if any(seed):
            raise ValueError('No seed accepted in off mode')
        return mode
    if env['GITHUB_REPOSITORY'] != 'chkuendig/Proton' or env['GITHUB_REF'] != BRANCH:
        raise ValueError('Stock proof bound to its reviewed feature ref/repository')
    if env['GITHUB_EVENT_NAME'] != 'workflow_dispatch' or env['GITHUB_RUN_ATTEMPT'] != '1':
        raise ValueError('Proof requires explicit attempt1 dispatch; no replay')
    if mode == 'cold' and any(seed):
        raise ValueError('Cold proof may not consume a seed')
    if mode == 'warm' and (not all(re.fullmatch(r'[1-9][0-9]*', x) for x in seed[:2]) or
                           not re.fullmatch(r'[a-f0-9]{64}', seed[2]) or
                           not re.fullmatch(r'[a-f0-9]{40}', seed[3])):
        raise ValueError('Warm proof requires exact run/artifact/digest/source head')
    if mode == 'warm' and seed[0] == env['GITHUB_RUN_ID']:
        raise ValueError('Cold and warm must be separate runs')
    return mode


def fetch(env, output):
    if validate(env) != 'warm' or output.exists():
        raise ValueError('Fetch needs warm mode and fresh seed directory')
    rid, aid, wanted = (env[k] for k in ('STOCK_SEED_RUN', 'STOCK_SEED_ARTIFACT', 'STOCK_SEED_DIGEST'))
    prefix = 'repos/chkuendig/Proton/actions/'
    run = api(prefix + 'runs/' + rid)
    if any(run.get(k) != v for k, v in {
            'head_sha': env['STOCK_SEED_HEAD'], 'head_branch': BRANCH.removeprefix('refs/heads/'),
            'status': 'completed', 'conclusion': 'success', 'run_attempt': 1,
            'event': 'workflow_dispatch', 'path': '.github/workflows/zluda.yml'}.items()):
        raise ValueError('Cold seed run binding/status mismatch')
    artifact = api(prefix + 'artifacts/' + aid)
    if (artifact['name'] != f'proton-stock-seed-{rid}-1' or artifact['expired'] or
            artifact['workflow_run']['id'] != int(rid) or
            artifact['workflow_run']['head_sha'] != env['STOCK_SEED_HEAD'] or
            artifact['digest'] != 'sha256:' + wanted or not 0 < artifact['size_in_bytes'] <= 4 * 1024**3):
        raise ValueError('Pinned artifact binding/digest/size mismatch')
    if shutil.disk_usage(output.parent).free < 2 * artifact['size_in_bytes'] + 1024**3:
        raise ValueError('Insufficient room for seed download')
    archive = output.with_suffix('.zip')
    with archive.open('xb') as stream:
        subprocess.run(['gh', 'api', prefix + 'artifacts/' + aid + '/zip'],
                       stdout=stream, check=True, timeout=600)
    if digest(archive) != wanted or archive.stat().st_size != artifact['size_in_bytes']:
        raise ValueError('Downloaded artifact bytes differ')
    output.mkdir()
    with zipfile.ZipFile(archive) as z:
        rows = z.infolist()
        if len(rows) != len(FILES) or {r.filename for r in rows} != FILES or sum(
                r.file_size for r in rows) > 8 * 1024**3:
            raise ValueError('Unexpected seed ZIP coverage/size')
        for row in rows:
            mode = row.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError('Non-regular seed ZIP member')
            with z.open(row) as src, (output / row.filename).open('xb') as dest:
                shutil.copyfileobj(src, dest)
    before = json.loads((output / 'STOCK-INPUT-BEFORE.json').read_text())
    after = json.loads((output / 'STOCK-INPUT-AFTER.json').read_text())
    manifest = json.loads((output / 'STOCK-EXPORT.json').read_text())
    if before != after or before != manifest['identity'] or digest(output / 'stock.tar.gz') != manifest['archive_sha256']:
        raise ValueError('Seed source/toolchain pre/post or inner archive differs')
    (output.parent / 'STOCK-SEED-VERIFIED.json').write_text(json.dumps(
        {'run': run, 'artifact': artifact, 'zip_sha256': wanted, 'PASS': True}, indent=2) + '\n')
    archive.unlink()


if __name__ == '__main__':
    mode = validate(os.environ)
    if sys.argv[1:] == ['check']:
        print('stock proof mode: ' + mode)
    elif len(sys.argv) == 3 and sys.argv[1] == 'fetch':
        fetch(os.environ, Path(sys.argv[2]))
    else:
        raise ValueError('Expected check or fetch DIRECTORY')
