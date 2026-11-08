#!/usr/bin/env python3
"""Small fixtures: hostile archives, source binding, SDK drift and comparison scope."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import os
import shutil
import subprocess
import sys
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if os.environ.get('GITHUB_ACTIONS') and not shutil.which('make'):
    raise RuntimeError('GNU make is mandatory in CI; make graph checks may not skip')
spec = importlib.util.spec_from_file_location('stock', ROOT / 'make/zluda-stock.py')
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, alter=None, extra=None):
        src = self.root / 'payload'; src.mkdir()
        for n in stock.ROOTS:
            (src / n).mkdir()
        (src / 'dist/tool').write_bytes(b'\x7fELFfixture')
        (src / 'dist/tool').chmod(0o755)
        (src / 'dist/alias').symlink_to('tool')
        rows = stock.tree(src)
        record = {'schema': stock.SCHEMA, 'identity': {'digest': 'pinned'}, 'files': copy.deepcopy(rows)}
        if alter:
            alter(record)
        path = self.root / 'stock.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            data = stock.encoded(record); m = tarfile.TarInfo('MANIFEST.json'); m.size = len(data)
            archive.addfile(m, io.BytesIO(data))
            for n in rows:
                archive.add(src / n, arcname=n, recursive=False)
            if extra:
                m, data = extra; archive.addfile(m, io.BytesIO(data) if data is not None else None)
        out = self.root / 'out'; out.mkdir()
        return path, out, record

    def test_valid_archive_exact_bytes_links_modes(self):
        archive, out, record = self.archive()
        self.assertEqual(stock.unpack(archive, out), record)
        self.assertEqual(stock.tree(out), record['files'])

    def test_archive_changed_digest(self):
        archive, out, _ = self.archive(lambda r: r['files']['dist/tool'].update(sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'bytes/metadata'):
            stock.unpack(archive, out)

    def test_unlisted_member(self):
        m = tarfile.TarInfo('dist/extra'); m.size = 1
        archive, out, _ = self.archive(extra=(m, b'x'))
        with self.assertRaisesRegex(ValueError, 'coverage'):
            stock.unpack(archive, out)

    def test_duplicate_member(self):
        m = tarfile.TarInfo('dist/tool'); m.size = 1
        archive, out, _ = self.archive(extra=(m, b'x'))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            stock.unpack(archive, out)

    def test_traversal_member(self):
        m = tarfile.TarInfo('../outside'); m.size = 1
        archive, out, _ = self.archive(extra=(m, b'x'))
        with self.assertRaises(ValueError):
            stock.unpack(archive, out)
        self.assertFalse((self.root / 'outside').exists())

    def test_link_escape(self):
        archive, out, _ = self.archive(lambda r: r['files']['dist/alias'].update(target='../../escape'))
        with self.assertRaisesRegex(ValueError, 'leaves component'):
            stock.unpack(archive, out)

    def test_link_as_parent(self):
        m=tarfile.TarInfo('dist/alias/child');m.size=0
        archive, out, _ = self.archive(lambda r: r['files'].update({'dist/alias/child': {'type': 'file', 'size': 0}}),extra=(m,b''))
        with self.assertRaisesRegex(ValueError,'non-directory parent'):
            stock.unpack(archive, out)

    def test_setuid_mode(self):
        archive, out, _ = self.archive(lambda r: r['files']['dist/tool'].update(mode=0o4755))
        with self.assertRaisesRegex(ValueError, 'mode'):
            stock.unpack(archive, out)

    def test_hardlink_member(self):
        m = tarfile.TarInfo('dist/link'); m.type = tarfile.LNKTYPE; m.linkname = 'dist/tool'
        archive, out, _ = self.archive(lambda r:r['files'].update({'dist/link':{'type':'file','mode':0o644,'size':0,'sha256':'unused'}}),extra=(m, None))
        with self.assertRaisesRegex(ValueError,'Member type'):
            stock.unpack(archive, out)

    def test_sdk_copy_refuses_external_symlink(self):
        outside = self.root / 'outside'; outside.write_text('x')
        build = self.root / 'build'; build.mkdir(); (build / 'link').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            stock.copy_checked(build / 'link', self.root / 'copy', [build])

    def test_import_identity_mismatch_leaves_build_empty(self):
        archive, _, _ = self.archive(); build = self.root / 'build'; build.mkdir()
        with patch.object(stock, 'identity', return_value={'digest': 'different'}):
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                stock.load_stock(self.root, build, archive, stock.sha(archive))
        self.assertEqual(list(build.iterdir()), [])

    def test_import_nonempty_build(self):
        archive, _, _ = self.archive(); build = self.root / 'build'; build.mkdir(); (build / 'owner').write_text('x')
        with self.assertRaisesRegex(ValueError, 'fresh empty'):
            stock.load_stock(self.root, build, archive, stock.sha(archive))
        self.assertEqual((build / 'owner').read_text(), 'x')

    def test_import_bad_digest_no_mutation(self):
        archive, _, _ = self.archive(); build = self.root / 'build'; build.mkdir()
        with self.assertRaisesRegex(ValueError, 'digest'):
            stock.load_stock(self.root, build, archive, '0'*64)
        self.assertEqual(list(build.iterdir()), [])

    def test_extra_sdk_header_refused(self):
        build = self.root / 'build'; (build / 'zluda-wine-sdk').mkdir(parents=True)
        (build / 'zluda-wine-sdk/extra.h').write_text('x')
        stock.write(build / 'STOCK-READY.json', {'schema': stock.SCHEMA, 'identity': {}, 'files': {}})
        with patch.object(stock, 'identity', return_value={}):
            with self.assertRaisesRegex(ValueError, 'coverage drift'):
                stock.ready(self.root, build)

    def portable_build(self):
        source = self.root / 'source'; build = self.root / 'build'
        (source / 'make').mkdir(parents=True); (build / 'dist/files/bin').mkdir(parents=True)
        (build / 'dist/files/bin/wine').write_bytes(b'\x7fELFwine')
        manifest = build / 'dist/files/share/openxr/wineopenxr64.json'
        manifest.parent.mkdir(parents=True); manifest.write_text('{"file_format_version":"1.0.0"}')
        (build / 'dist/version').write_text('time assembly')
        (build / 'dist/LICENSE').write_text('candidate source ID')
        prepared = 'dlls/ntdll/callback.c'
        files = {prepared: hashlib.sha256(b'prepared').hexdigest()}
        contract = {'wine_pin':'wine-pin', 'prepared_files':files}
        stock.write(source / 'make/zluda-wine-abi.json', contract)
        for name,data in [(prepared,b'prepared'),('dlls/nvcuda/Makefile.in',b'stub recipe'),('dlls/nvcuda/nvcuda.spec',b'')]:
            path=build/'src-wine'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        (build/'src-wine/unused-cold-header.h').write_text('not in the exported interface')
        sdk=build/'zluda-wine-sdk'
        for name,data in [('include/windef.h',b'header'),('include/wine/server_protocol.h',b'protocol'),
                          ('include-generated/wtypes.h',b'generated'),('bin/winebuild',b'\x7fELFtool'),
                          ('lib/ntdll.so',b'\x7fELFprovider')]:
            path=sdk/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        flags=['callback_id_zero','same_thread_destructor','process_exit','thread_exit',
               'loader_lock_refusal','locally_scoped_strong_provider','eager_binding']
        stock.write(sdk/'ABI-RECEIPT.json',{'contract':'zluda-wine-prepared-abi-2','wine_pin':'wine-pin',
                    'prepared_files':files,'verified':dict.fromkeys(flags,True),
                    'sdk_files':{n:stock.sha(sdk/n) for n in ['bin/winebuild','lib/ntdll.so']}})
        for arch in ['i386','x86_64']:
            path=build/f'dst-wine-{arch}/lib/wine/{arch}-windows/nvcuda.dll'
            path.parent.mkdir(parents=True);path.write_bytes(b'MZrawstub')
        return source,build

    def test_missing_runtime_manifest_refuses_export_before_mutation(self):
        source,build=self.portable_build(); archive=self.root/'export.tar.gz'
        (build/'dist/files/share/openxr/wineopenxr64.json').unlink()
        before=stock.tree(build)
        with self.assertRaisesRegex(ValueError,'Missing installed runtime manifest'):
            stock.export(source,build,archive)
        self.assertEqual(stock.tree(build),before)
        self.assertFalse(archive.exists())

    def test_import_missing_runtime_manifest_leaves_build_empty(self):
        archive,_,_=self.archive(); build=self.root/'build';build.mkdir()
        with patch.object(stock,'identity',return_value={'digest':'pinned'}):
            with self.assertRaisesRegex(ValueError,'Missing installed runtime manifest'):
                stock.load_stock(self.root,build,archive,stock.sha(archive))
        self.assertEqual(list(build.iterdir()),[])

    def test_runtime_manifest_symlink_refused(self):
        _,build=self.portable_build(); path=build/'dist/files/share/openxr/wineopenxr64.json'
        path.unlink();path.symlink_to('../../bin/wine')
        with self.assertRaisesRegex(ValueError,'Missing installed runtime manifest'):
            stock.validate_runtime(build)

    def test_prelaunch_inventory_keeps_full_tree_without_qualification(self):
        build=self.root/'build';redist=build/'redist';redist.mkdir(parents=True)
        (redist/'runtime.json').write_text('{"runtime":"fixture"}')
        (redist/'probe').write_bytes(b'\x7fELFfixture');(redist/'probe').chmod(0o755)
        (redist/'alias').symlink_to('probe')
        output=self.root/'inventory.json';before=stock.tree(redist)
        subprocess.run([sys.executable,'-B',str(ROOT/'make/zluda-stock.py'),
                        'inventory',str(build),str(output)],check=True,capture_output=True)
        row=json.loads(output.read_text())
        self.assertEqual(row['installed_files'],before)
        self.assertEqual(stock.tree(redist),before)
        self.assertIn('not qualification',row['evidence_kind'])

    def test_export_import_roundtrip_and_ready(self):
        source,build=self.portable_build(); archive=self.root/'export.tar.gz'
        with patch.object(stock,'identity',return_value={'digest':'pinned'}), patch.object(
                stock.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3)):
            stock.export(source,build,archive)
            exported=json.loads((build/'STOCK-READY.json').read_text())
            self.assertNotIn('dist/version',exported['files']);self.assertNotIn('dist/LICENSE',exported['files'])
            stock.ready(source,build)
            self.assertFalse((build/'src-wine/unused-cold-header.h').exists())
            warm=self.root/'warm';warm.mkdir();stock.load_stock(source,warm,archive,stock.sha(archive))
            imported=json.loads((warm/'STOCK-READY.json').read_text())
            self.assertEqual(exported,imported)
            stock.ready(source,warm)
            (warm/'dst-wine-i386/lib/wine/i386-windows/nvcuda.dll').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'coverage drift'):
                stock.ready(source,warm)

    def test_added_source_or_raw_install_file_refused(self):
        source,build=self.portable_build(); archive=self.root/'export.tar.gz'
        with patch.object(stock,'identity',return_value={'digest':'pinned'}), patch.object(
                stock.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3)):
            stock.export(source,build,archive)
            for name in ['src-wine','dst-wine-i386','dst-wine-x86_64','zluda-wine-sdk']:
                with self.subTest(root=name):
                    extra=build/name/'extra';extra.write_text('shadow input')
                    with self.assertRaisesRegex(ValueError,'coverage drift'):stock.ready(source,build)
                    extra.unlink()

    def test_make_missing_in_ci_is_fatal(self):
        e=dict(os.environ,GITHUB_ACTIONS='true',PATH='')
        r=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve())],env=e,text=True,capture_output=True)
        self.assertNotEqual(r.returncode,0)
        self.assertIn('GNU make is mandatory in CI',r.stderr)

    def test_missing_abi_flag_refused(self):
        source,build=self.portable_build();p=build/'zluda-wine-sdk/ABI-RECEIPT.json';r=json.loads(p.read_text())
        r['verified']={};stock.write(p,r)
        with self.assertRaisesRegex(ValueError,'flags mismatch'):stock.validate_sdk(source,build)


def base():
    return {'stock_identity': {}, 'stock_files': {'stock': {'sha256': 'same'}},
            'installed_files': {'files/lib/runtime.so': {'type': 'file', 'mode': 0o755, 'sha256': 'same', 'binary': True},
                                'version': {'type': 'file', 'mode': 0o644, 'sha256': 'v1', 'binary': False},
                                'files/share/default_pfx/system.reg': {'type': 'file', 'mode': 0o644, 'sha256': 'r1', 'binary': False}},
            'version_text': '100 commit\n', 'registry_text': {'files/share/default_pfx/system.reg': '[key] 100\n'},
            'wineboot_auxiliary_hex': {}}


class Comparison(unittest.TestCase):
    def auxiliary(self, row, name, data, mode=0o644):
        row['installed_files'][name]={'type':'file','mode':mode,'size':len(data),
                                     'sha256':hashlib.sha256(data).hexdigest(),'binary':bool(mode & 0o111)}
        row['wineboot_auxiliary_hex'][name]=data.hex()

    def compare(self, a, b):
        with tempfile.TemporaryDirectory() as tmp:
            x, y = Path(tmp)/'a', Path(tmp)/'b'; stock.write(x,a); stock.write(y,b)
            return stock.compare(x,y)

    def test_equal(self):
        self.assertEqual(self.compare(base(), base())['equivalence'], 'PASS')

    def test_binary_change_always_fails(self):
        b=base(); b['installed_files']['files/lib/runtime.so']['sha256']='bad'
        self.assertEqual(self.compare(base(),b)['equivalence'], 'FAIL')

    def test_binary_removed_fails(self):
        b=base(); del b['installed_files']['files/lib/runtime.so']
        self.assertEqual(self.compare(base(),b)['equivalence'], 'FAIL')

    def test_stock_difference_fails(self):
        b=base(); b['stock_files']['stock']['sha256']='bad'
        self.assertEqual(self.compare(base(),b)['equivalence'], 'FAIL')

    def test_version_timestamp_only_allowed(self):
        b=base(); b['installed_files']['version']['sha256']='v2'; b['version_text']='200 commit\n'
        self.assertEqual(self.compare(base(),b)['equivalence'], 'PASS')

    def test_version_commit_change_fails(self):
        b=base(); b['installed_files']['version']['sha256']='v2'; b['version_text']='200 different\n'
        self.assertEqual(self.compare(base(),b)['equivalence'], 'FAIL')

    def test_registry_delta_never_automatically_passes(self):
        b=base(); n='files/share/default_pfx/system.reg'; b['installed_files'][n]['sha256']='r2'; b['registry_text'][n]='[key] 200\n'
        r=self.compare(base(),b)
        self.assertEqual(r['equivalence'],'NEEDS_REGISTRY_REVIEW'); self.assertIn(n,r['registry_requires_manual_classification'])
        self.assertIn('-[key] 100',r['all_full_tree_differences'][n]['raw_diff'])

    def test_nonprefix_registry_fails(self):
        a=base(); b=base(); n='files/other.reg'
        a['installed_files'][n]={'type':'file','mode':0o644,'sha256':'a'}
        b['installed_files'][n]={'type':'file','mode':0o644,'sha256':'b'}
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_prefix_binary_fails(self):
        a=base(); b=base(); n='files/share/default_pfx/drive_c/windows/system32/nvcuda.dll'
        a['installed_files'][n]={'type':'file','mode':0o644,'sha256':'a','binary':True}
        b['installed_files'][n]={'type':'file','mode':0o644,'sha256':'b','binary':True}
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_mode_difference_fails(self):
        b=base(); b['installed_files']['version']['mode']=0o755
        self.assertEqual(self.compare(base(),b)['equivalence'],'FAIL')

    def test_update_timestamp_needs_raw_review(self):
        a,b=base(),base();n='files/share/default_pfx/.update-timestamp'
        self.auxiliary(a,n,b'cold time');self.auxiliary(b,n,b'warm time')
        r=self.compare(a,b);self.assertEqual(r['equivalence'],'NEEDS_REGISTRY_REVIEW')
        self.assertEqual(r['all_full_tree_differences'][n]['raw_evidence_hex']['cold'],b'cold time'.hex())

    def test_update_timestamp_mode_change_fails(self):
        a,b=base(),base();n='files/share/default_pfx/.update-timestamp'
        self.auxiliary(a,n,b'cold time');self.auxiliary(b,n,b'warm time',0o600)
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_update_timestamp_type_change_fails(self):
        a,b=base(),base();n='files/share/default_pfx/.update-timestamp'
        self.auxiliary(a,n,b'cold time');b['installed_files'][n]={'type':'link','mode':0o644,'target':'elsewhere'}
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_missing_update_timestamp_fails(self):
        a,b=base(),base();self.auxiliary(a,'files/share/default_pfx/.update-timestamp',b'cold time')
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def msi_pair(self, content=b'identical msi'):
        a,b=base(),base();directory='files/share/default_pfx/drive_c/windows/Installer/'
        self.auxiliary(a,directory+'1234.msi',b'identical msi');self.auxiliary(b,directory+'abcd.msi',content)
        return a,b,directory

    def test_exactly_one_identical_msi_rename_needs_review(self):
        a,b,d=self.msi_pair();r=self.compare(a,b)
        self.assertEqual(r['equivalence'],'NEEDS_REGISTRY_REVIEW');self.assertEqual(r['forbidden_differences'],[])
        self.assertEqual(set(r['wineboot_requires_manual_classification']),{d+'1234.msi',d+'abcd.msi'})
        self.assertEqual(r['wineboot_installer_rename_evidence'][0]['content_hex'],b'identical msi'.hex())

    def test_msi_changed_content_fails(self):
        a,b,_=self.msi_pair(b'changed msi');self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_more_than_one_msi_fails(self):
        a,b,d=self.msi_pair()
        for r in [a,b]:self.auxiliary(r,d+'4567.msi',b'additional unchanged msi')
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_msi_rename_mode_change_fails(self):
        a,b,d=self.msi_pair();b['installed_files'][d+'abcd.msi']['mode']=0o600
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_msi_rename_size_change_fails(self):
        a,b,d=self.msi_pair();b['installed_files'][d+'abcd.msi']['size']+=1
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_msi_non_four_hex_name_fails(self):
        a,b,d=self.msi_pair();r=b['installed_files'].pop(d+'abcd.msi')
        b['installed_files'][d+'long-name.msi']=r
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_msi_other_directory_fails(self):
        a,b,d=self.msi_pair()
        for r,name in [(a,'1234.msi'),(b,'abcd.msi')]:
            r['installed_files']['files/elsewhere/'+name]=r['installed_files'].pop(d+name)
        self.assertEqual(self.compare(a,b)['equivalence'],'FAIL')

    def test_msi_raw_evidence_must_match_hash(self):
        a,b,d=self.msi_pair();b['wineboot_auxiliary_hex'][d+'abcd.msi']=b'wrong'.hex()
        with self.assertRaisesRegex(ValueError,'raw evidence'):self.compare(a,b)


@unittest.skipUnless(shutil.which('make'), 'GNU make unavailable; cloud runner executes graph checks')
class MakeGraph(unittest.TestCase):
    def run_make(self, reuse, target):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);binary=directory/'bin';binary.mkdir()
            for name,body in [('rsync','exit 0'),('cc','echo fixture-gcc')]:
                path=binary/name;path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o755)
            (directory/'query.mk').write_text('__stock_graph__:\n\t@:\n')
            env=dict(os.environ,PATH=str(binary)+':'+os.environ['PATH'])
            args=['make','-n','-j3','-f',str(ROOT/'Makefile.in'),'SRCDIR='+str(ROOT),'CONTAINER=1',
                  'BUILD_NAME=Proton-ZLUDA','INTERNAL_TOOL_NAME=Proton-ZLUDA',
                  'SOURCE_DATE_EPOCH=1791559305','BASE_SOURCE_DATE_EPOCH=1791559305']
            if reuse is True:args+=['ZLUDA_STOCK_REUSE=1']
            else:
                if reuse is False:args+=['ZLUDA_STOCK_ONLY=1']
                args+=['-p','-f',str(directory/'query.mk')]
            return subprocess.run(args+[target],cwd=directory,env=env,text=True,capture_output=True,timeout=20)

    def test_imported_wine_uses_verifier_without_configure(self):
        r=self.run_make(True,'wine');self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('zluda-stock.py',r.stdout);self.assertNotIn('configure',r.stdout)

    def test_stock_graph_excludes_candidate_and_prefix(self):
        r=self.run_make(False,'__stock_graph__');self.assertEqual(r.returncode,0,r.stderr)
        line=next(l for l in r.stdout.splitlines() if l.startswith('all-dist:'))
        self.assertNotIn('default_pfx',line.split());self.assertNotIn('zluda',line.split());self.assertNotIn('zluda-wine',line.split())

    def test_stock_carries_every_installed_prefix_dependency(self):
        ordinary=self.run_make(None,'__stock_graph__');self.assertEqual(ordinary.returncode,0,ordinary.stderr)
        cold=self.run_make(False,'__stock_graph__');self.assertEqual(cold.returncode,0,cold.stderr)
        prefix=next(l for l in ordinary.stdout.splitlines() if l.startswith('default_pfx:'))
        required={p.split('/dist/files/',1)[1] for p in prefix.split()[1:] if '/dist/files/' in p}
        self.assertTrue(required,'ordinary prefix must expose installed runtime prerequisites')
        all_dist=next(l for l in cold.stdout.splitlines() if l.startswith('all-dist:'))
        installed={p.split('/dist/files/',1)[1] for p in all_dist.split()[1:] if '/dist/files/' in p}
        self.assertFalse(required-installed,'stock omits ordinary installed prefix dependencies: '+repr(required-installed))


if __name__ == '__main__':
    unittest.main()
