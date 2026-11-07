#!/usr/bin/env python3
import os
import importlib.machinery
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
loader = importlib.machinery.SourceFileLoader('proton', str(Path(__file__).resolve().parents[2] / 'proton'))
policy = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(policy)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'make'))
import zluda as checks


class Policy(unittest.TestCase):
    def test_prefix_copies_detach_links_without_changing_the_supplier(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool, supplier = root / 'tool', root / 'supplier/lib/wine/x86_64-windows'
            supplier.mkdir(parents=True)
            compat = policy.CompatData(str(root / 'compatdata'))
            pairs = []
            for directory in policy.ARCHES:
                windows = Path(compat.prefix_dir) / 'drive_c/windows' / directory
                windows.mkdir(parents=True)
                for name in ('builtin.dll', 'icuin68.dll'):
                    donor = supplier / (directory + name)
                    donor.write_bytes(bytes(64) + b'Wine builtin DLL' if name == 'builtin.dll' else b'ICU fixture')
                    for kind in ('symlink', 'hardlink'):
                        path = windows / kind / name
                        path.parent.mkdir(exist_ok=True)
                        path.symlink_to(donor) if kind == 'symlink' else os.link(donor, path)
                        pairs.append((path, donor, donor.read_bytes()))
                template = windows / 'template'
                template.symlink_to(donor)
                with mock.patch.dict(os.environ, PROTON_DLL_COPY='*'):
                    compat.pfx_copy(str(template), str(windows / 'initial.dll'))
                template.unlink()
                self.assertFalse((windows / 'initial.dll').is_symlink())
            compat.isolate_module_links(tool)
            for path, donor, original in pairs:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.stat().st_nlink, 1)
                self.assertEqual(path.read_bytes(), original)
                path.write_bytes(b'Wine prefix update')
                self.assertEqual(donor.read_bytes(), original)
            failed = windows / 'failed.dll'
            failed.symlink_to(pairs[0][1])
            with mock.patch.object(policy.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
                with self.assertRaises(OSError):
                    compat.isolate_module_links(tool)
            self.assertEqual(pairs[0][1].read_bytes(), pairs[0][2])

    def test_module_directory_links_into_tools_refuse_isolation(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(policy, 'log'):
            root = Path(temporary)
            tool = root / 'tool'
            for directory in policy.ARCHES:
                for target in (tool, root / 'other-tool'):
                    target.mkdir(exist_ok=True)
                    (target / 'toolmanifest.vdf').touch()
                    donor = target / 'builtin.dll'
                    donor.write_bytes(b'unchanged')
                    before = (donor.read_bytes(), donor.stat())
                    compat = policy.CompatData(str(root / (directory + target.name)))
                    windows = Path(compat.prefix_dir) / 'drive_c/windows'
                    windows.mkdir(parents=True)
                    (windows / directory).symlink_to(target)
                    self.assertIs(compat.isolate_module_links(tool), False)
                    self.assertEqual((donor.read_bytes(), donor.stat()), before)

    def test_same_build_stubs_and_private_prefix_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool = Path(temporary)
            cuda = tool / 'files/lib/wine-cuda'
            hashes = {}
            for arch in policy.ARCHES.values():
                path = cuda / 'stub' / arch / 'nvcuda.dll'
                path.parent.mkdir(parents=True)
                path.write_bytes(('own Wine output: ' + arch).encode())
                hashes[arch] = hashlib.sha256(path.read_bytes()).hexdigest()
            record = dict(proton_pin=checks.VALVE_PROTON_PIN, wine_pin=checks.VALVE_WINE_PIN,
                          source_sha256=checks.VALVE_STUB_SOURCES, sha256=hashes)
            manifest = cuda / 'stubs.json'
            manifest.write_text(json.dumps(record))
            checks.stub_assets(tool)
            prefix = tool / 'prefix'
            policy.materialize_default_prefix(tool, prefix)
            before = [(path.stat().st_ino, path.stat().st_mtime_ns)
                      for path in prefix.rglob('nvcuda.dll')]
            policy.materialize_default_prefix(tool, prefix)
            self.assertEqual([(path.stat().st_ino, path.stat().st_mtime_ns)
                              for path in prefix.rglob('nvcuda.dll')], before)
            for directory, arch in policy.ARCHES.items():
                copy = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
                original = cuda / 'stub' / arch / 'nvcuda.dll'
                self.assertNotEqual(copy.stat().st_ino, original.stat().st_ino)
                copy.write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    checks.validate_default_prefix(tool, prefix)
                policy.materialize_default_prefix(tool, prefix)
                copy.unlink()
                copy.symlink_to(original)
                with self.assertRaises(ValueError):
                    checks.validate_default_prefix(tool, prefix)
                policy.materialize_default_prefix(tool, prefix)
            for field in ('proton_pin', 'wine_pin', 'source_sha256', 'sha256'):
                manifest.write_text(json.dumps(dict(record, **{field: {}})))
                with self.assertRaises(ValueError):
                    checks.stub_assets(tool)
            manifest.write_text(json.dumps(record))
            original.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                checks.stub_assets(tool)

    def test_settings_display_detection_and_explicit_opt_out(self):
        hardware = [([(0x030000, 0x1002)], '1'), ([], '0'),
                    ([(0x030000, 0x1002), (0x030200, 0x10de)], '0'),
                    ([(0x030000, 0x1002), (0x040300, 0x10de)], '1')]
        with tempfile.TemporaryDirectory() as temporary:
            for i, (devices, default) in enumerate(hardware):
                directory = Path(temporary) / str(i)
                directory.mkdir()
                for j, (cls, vendor) in enumerate(devices):
                    device = directory / str(j)
                    device.mkdir()
                    (device / 'class').write_text(hex(cls))
                    (device / 'vendor').write_text(hex(vendor))
                for app in policy.ZLUDA_TITLES:
                    for explicit in (None, '0', '1'):
                        for setting in (None, '0', 1):
                            env = {'SteamAppId': app}
                            if explicit is not None:
                                env['PROTON_ENABLE_ZLUDA'] = explicit
                            settings = {} if setting is None else {'PROTON_ENABLE_ZLUDA': setting}
                            expected = explicit if explicit is not None else str(setting) if setting is not None else default
                            with mock.patch.dict(os.environ, env, clear=True), \
                                 mock.patch.dict(sys.modules, {'user_settings': SimpleNamespace(user_settings=settings)}), \
                                 mock.patch.object(policy, 'Path', return_value=directory):
                                policy.early_title_policy(os.environ, 'run')
                                self.assertEqual(os.environ['PROTON_ENABLE_ZLUDA'], expected)
                                self.assertEqual(os.environ.get('PROTON_USE_WOW64'), '1' if expected == '1' and app in policy.PE32_TITLES else None)
        env = {'SteamAppId': '49520', 'PROTON_ENABLE_ZLUDA': '1'}
        policy.early_title_policy(env, 'runinprefix')
        self.assertNotIn('PROTON_USE_WOW64', env)

    def test_mirror_moves_only_with_system_core_and_restores_backups(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(policy, 'log'):
            root = Path(temporary)
            game, prefix = root / 'game', root / 'prefix'
            binaries = game / 'Binaries'
            (binaries / 'PhysXLocal').mkdir(parents=True)
            files = {name: name.encode() for name in
                     ('PhysXCore.dll', 'PhysXDevice.dll', 'PhysXLocal/PhysXLoader.dll')}
            for name, data in files.items():
                (binaries / name).write_bytes(data)
            core = prefix / 'drive_c/Program Files (x86)/NVIDIA Corporation/PhysX/Engine/v2.8.0/PhysXCore.dll'
            session = SimpleNamespace(env={'SteamAppId': '17410', 'STEAM_COMPAT_INSTALL_PATH': str(game)})
            compat = SimpleNamespace(prefix_dir=str(prefix))
            argv = ['proton', 'run', str(binaries / 'MirrorsEdge.exe')]
            with mock.patch.object(policy, 'g_compatdata', compat, create=True), \
                 mock.patch.object(policy, 'ensure_physx'), \
                 mock.patch.object(policy.subprocess, 'run', side_effect=AssertionError('installer invoked')):
                policy.Session.prepare_zluda_title(session, argv)
                self.assertEqual({name: (binaries / name).read_bytes() for name in files}, files)
                core.parent.mkdir(parents=True)
                core.write_bytes(b'system PhysX core')
                for enabled in ('1', '0'):
                    session.env['PROTON_ENABLE_ZLUDA'] = enabled
                    policy.Session.prepare_zluda_title(session, argv)
                    for name, data in files.items():
                        parts = Path(name).parts
                        backup = binaries / (parts[0] + '.bak')
                        self.assertFalse((binaries / parts[0]).exists())
                        self.assertEqual(backup.joinpath(*parts[1:]).read_bytes(), data)
                core.unlink()
                for enabled in ('1', '0'):
                    session.env['PROTON_ENABLE_ZLUDA'] = enabled
                    policy.Session.prepare_zluda_title(session, argv)
                    self.assertEqual({name: (binaries / name).read_bytes() for name in files}, files)
                    self.assertFalse(list(binaries.glob('*.bak')))

    def test_bl2_keeps_launcher_swap_no_splash_and_no_esync(self):
        env = {'SteamAppId': '49520', 'PROTON_ENABLE_ZLUDA': '1'}
        with mock.patch.object(policy, 'game_setting', return_value='1'), \
             mock.patch.object(policy, 'ensure_physx'), \
             mock.patch.object(policy.subprocess, 'run', side_effect=AssertionError('installer invoked')):
            policy.early_title_policy(env, 'run')
            argv = ['proton', 'run', 'Z:/game/Launcher.exe']
            for _ in range(2):
                policy.Session.prepare_zluda_title(SimpleNamespace(env=env), argv)
            self.assertEqual(argv, ['proton', 'run', 'Z:/game/Borderlands2.exe', '-NoSplash'])
            self.assertEqual(env['PROTON_NO_ESYNC'], '1')
            self.assertEqual(env['PROTON_USE_WOW64'], '1')

    def test_deck_detection_fails_closed_and_preserves_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dmi, nodes = root / 'dmi', root / 'nodes'
            dmi.mkdir(); nodes.mkdir()
            for name, value in [('sys_vendor', 'Valve'), ('product_name', 'Jupiter'), ('board_name', 'Galileo')]:
                (dmi / name).write_text(value)
            for values in ([0, 100303], [100303, 100303], [100302], ['bad'], []):
                for node in nodes.iterdir():
                    shutil.rmtree(node)
                for i, value in enumerate(values):
                    node = nodes / str(i)
                    node.mkdir()
                    (node / 'properties').write_text('gfx_target_version ' + str(value))
                for on in ('0', '1'):
                    env = {'PROTON_ENABLE_ZLUDA': on}
                    with mock.patch.object(policy, 'Path', side_effect=lambda name: dmi if name == '/sys/class/dmi/id' else nodes):
                        policy.configure_device(env)
                    self.assertEqual(env.get('HSA_OVERRIDE_GFX_VERSION'), '10.3.0' if on == '1' and values == [0, 100303] else None)
            env = {'PROTON_ENABLE_ZLUDA': '1', 'HSA_OVERRIDE_GFX_VERSION': 'user'}
            policy.configure_device(env)
            self.assertEqual(env['HSA_OVERRIDE_GFX_VERSION'], 'user')


class PhysX(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tool = SimpleNamespace(base_dir=str(self.root / 'tool'), wine_bin='/owned/tool/wine64',
                                    wineserver_bin='/owned/tool/wineserver')
        self.env = dict(SteamAppId='35140', PROTON_ENABLE_ZLUDA='1', WINEARCH='wow64',
                        WINEPREFIX=str(self.root / 'prefix'), XDG_CACHE_HOME=str(self.root / 'cache'),
                        STEAM_COMPAT_INSTALL_PATH=str(self.root / 'game'),
                        ZLUDA_WINE_BACKEND_PATH='inherited backend', WINEDLLOVERRIDES='nvcuda=n',
                        LD_LIBRARY_PATH=str(Path(self.tool.base_dir) / 'files/lib64'),
                        LD_PRELOAD='inherited Steam overlay')
        self.data = b'fake PhysX installer'
        self.cache = self.root / 'cache/proton-zluda/physx' / policy.PHYSX_URL.rsplit('/', 1)[-1]
        self.query = SimpleNamespace(stdout='', returncode=1)
        self.installer = mock.Mock()
        self.waiter = mock.Mock()
        self.download = mock.Mock(side_effect=lambda *a, **k: io.BytesIO(self.data))
        self.log = mock.Mock()
        def wine(args, **options):
            if args[0] == self.tool.wineserver_bin:
                self.assertEqual(args[1:], ['-w'])
                self.assertEqual(options['timeout'], 180)
                self.assertTrue(options['check'])
                self.waiter(args, **options)
                return SimpleNamespace(returncode=0)
            self.assertEqual(args[0], self.tool.wine_bin)
            if args[1].endswith('reg.exe'):
                self.assertEqual(args[1:], [r'C:\windows\syswow64\reg.exe', 'query',
                                 r'HKLM\Software\AGEIA Technologies', '/v', 'PhysX Version', '/reg:32'])
                return self.query
            self.assertEqual(args[1:], [str(self.cache), '/s'])
            self.assertEqual(self.cache.read_bytes(), self.data)
            self.assertTrue(options['check'])
            self.assertEqual(options['timeout'], 180)
            self.installer(args, **options)
            return SimpleNamespace(returncode=0)
        for target, name, value in [(policy, 'g_proton', self.tool),
                                    (policy, 'g_compatdata', SimpleNamespace(prefix_dir=self.env['WINEPREFIX'])),
                                    (policy, 'PHYSX_SHA256', hashlib.sha256(self.data).hexdigest()),
                                    (policy.urllib.request, 'urlopen', self.download),
                                    (policy.subprocess, 'run', wine), (policy, 'log', self.log)]:
            patcher = mock.patch.object(target, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_absent_runtime_installs_from_one_verified_shared_cache(self):
        for appid in ('17410', '35140', '49520'):
            self.env['SteamAppId'] = appid
            self.env['WINEPREFIX'] = str(self.root / appid)
            policy.ensure_physx(self.env)
            helper = self.installer.call_args.kwargs['env']
            self.assertEqual(helper['WINEPREFIX'], self.env['WINEPREFIX'])
            self.assertEqual(helper['WINEARCH'], 'wow64')
            self.assertEqual(helper['PROTON_ENABLE_ZLUDA'], '1')
            self.assertNotIn('ZLUDA_WINE_BACKEND_PATH', helper)
            self.assertEqual(helper['LD_LIBRARY_PATH'], '')
            self.assertEqual(helper['LD_PRELOAD'], '')
            self.assertEqual(helper['WINEDLLOVERRIDES'], self.env['WINEDLLOVERRIDES'])
            self.assertTrue(helper['WINEDLLPATH'].endswith('/wine-cuda/stub'))
            self.assertEqual(self.waiter.call_args.kwargs['env'], helper)
        self.assertEqual(self.installer.call_count, 3)
        self.assertEqual(self.waiter.call_count, 3)
        self.download.assert_called_once_with(policy.PHYSX_URL, timeout=30)
        self.assertEqual(self.env['PROTON_ENABLE_ZLUDA'], '1')
        self.log.assert_not_called()

    def test_installed_version_boundary_preserves_newer_steam_runtimes(self):
        for version in (9131220, 9140701, 9140702, 9231019, 9260703, 10000000):
            with self.subTest(version=version):
                self.installer.reset_mock()
                self.query.stdout = '    PhysX Version    REG_DWORD    ' + hex(version) + '\n'
                policy.ensure_physx(self.env)
                self.assertEqual(self.installer.call_count, int(version < 9140702))

    def test_cached_hash_mismatch_skips_without_redownload_or_execution(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_bytes(b'corrupt fixture')
        policy.ensure_physx(self.env)
        self.installer.assert_not_called()
        self.download.assert_not_called()
        self.log.assert_called_once()
        self.assertIn('SHA-256 mismatch', self.log.call_args.args[0])
        self.assertFalse(self.cache.exists())

    def test_download_hash_mismatch_never_caches_or_executes_bytes(self):
        self.download.side_effect = lambda *a, **k: io.BytesIO(b'wrong download fixture')
        policy.ensure_physx(self.env)
        self.installer.assert_not_called()
        self.log.assert_called_once()
        self.assertIn('SHA-256 mismatch', self.log.call_args.args[0])
        self.assertFalse(self.cache.exists())

    def test_download_is_flushed_and_synced_before_atomic_cache_publication(self):
        temporary = self.cache.with_suffix('.tmp')
        fsync, replace = policy.os.fsync, policy.os.replace
        events = []
        def sync(fd):
            self.assertFalse(self.cache.exists())
            self.assertEqual(temporary.read_bytes(), self.data)
            fsync(fd)
            events.append('fsync')
        def publish(source, destination):
            self.assertEqual(events, ['fsync'])
            self.assertFalse(self.cache.exists())
            self.assertEqual(Path(source), temporary)
            self.assertEqual(Path(destination), self.cache)
            replace(source, destination)
            events.append('rename')
        with mock.patch.object(policy.os, 'fsync', side_effect=sync), \
                mock.patch.object(policy.os, 'replace', side_effect=publish):
            policy.ensure_physx(self.env)
        self.assertEqual(events, ['fsync', 'rename'])
        self.assertEqual(self.cache.read_bytes(), self.data)
        self.assertFalse(temporary.exists())
        self.installer.assert_called_once()

    def test_failed_cache_sync_or_rename_never_publishes_or_executes(self):
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                self.log.reset_mock()
                with mock.patch.object(policy.os, operation, side_effect=OSError('cache fixture')):
                    policy.ensure_physx(self.env)
                self.assertFalse(self.cache.exists())
                self.installer.assert_not_called()
                self.waiter.assert_not_called()
                self.log.assert_called_once()

    def test_download_exit_and_timeout_failures_log_once_and_continue(self):
        cases = [(self.download, OSError('offline fixture')),
                 (self.installer, policy.subprocess.CalledProcessError(1, 'fake installer')),
                 (self.installer, policy.subprocess.TimeoutExpired('fake installer', 180))]
        for target, failure in cases:
            with self.subTest(failure=failure):
                self.log.reset_mock()
                self.waiter.reset_mock()
                previous = target.side_effect
                target.side_effect = failure
                policy.ensure_physx(self.env)
                target.side_effect = previous
                self.log.assert_called_once()
                self.assertIn('continuing launch', self.log.call_args.args[0])
                self.assertEqual(self.waiter.call_count, int(target is self.installer))

    def test_wait_timeout_logs_and_continues_without_terminating_wineserver(self):
        self.waiter.side_effect = policy.subprocess.TimeoutExpired([self.tool.wineserver_bin, '-w'], 180)
        policy.ensure_physx(self.env)
        self.installer.assert_called_once()
        self.waiter.assert_called_once()
        self.log.assert_called_once()
        self.assertIn('continuing launch', self.log.call_args.args[0])
        self.assertIn('wineserver', self.log.call_args.args[0])

    def test_only_the_three_enabled_game_policies_request_setup(self):
        with mock.patch.object(policy, 'ensure_physx') as setup:
            for appid in policy.ZLUDA_TITLES | {'other'}:
                for enabled in ('0', '1'):
                    for verb in ('run', 'waitforexitandrun', 'runinprefix'):
                        setup.reset_mock()
                        self.env.update(SteamAppId=appid, PROTON_ENABLE_ZLUDA=enabled)
                        policy.Session.prepare_zluda_title(SimpleNamespace(env=self.env), ['proton', verb, 'game.exe'])
                        self.assertEqual(setup.call_count, int(enabled == '1' and verb != 'runinprefix'
                                         and appid in {'17410', '35140', '49520'}))

    def test_steam_installscript_evaluator_never_provisions_physx(self):
        with mock.patch.object(policy, 'ensure_physx') as setup:
            for appid in ('17410', '35140', '49520'):
                self.env['SteamAppId'] = appid
                for verb in ('run', 'waitforexitandrun'):
                    for executable in ('iscriptevaluator.exe', '/steam/bin/iscriptevaluator.exe',
                                       r'Z:\Steam\bin\IScriptEvaluator.exe'):
                        policy.Session.prepare_zluda_title(SimpleNamespace(env=self.env),
                                                           ['proton', verb, executable, 'installscript.vdf'])
            setup.assert_not_called()

    def test_launch_without_an_executable_never_provisions_physx(self):
        with mock.patch.object(policy, 'ensure_physx') as setup:
            for appid in ('17410', '35140', '49520'):
                self.env['SteamAppId'] = appid
                for verb in ('run', 'waitforexitandrun'):
                    policy.Session.prepare_zluda_title(SimpleNamespace(env=self.env), ['proton', verb])
            setup.assert_not_called()

    def test_installer_children_finish_before_mirror_system_core_guard(self):
        binaries = self.root / 'game/Binaries'
        binaries.mkdir(parents=True)
        device = binaries / 'PhysXDevice.dll'
        device.write_bytes(b'bundled fixture')
        core = self.root / 'prefix/drive_c/Program Files (x86)/NVIDIA Corporation/PhysX/Engine/v2.8.0/PhysXCore.dll'
        def install(*args, **kwargs):
            self.assertFalse(core.exists())
        def finish(*args, **kwargs):
            self.installer.assert_called_once()
            core.parent.mkdir(parents=True)
            core.write_bytes(b'installed core fixture')
        self.installer.side_effect = install
        self.waiter.side_effect = finish
        self.env['SteamAppId'] = '17410'
        policy.Session.prepare_zluda_title(SimpleNamespace(env=self.env), ['proton', 'run', 'MirrorsEdge.exe'])
        self.assertFalse(device.exists())
        self.assertEqual((binaries / 'PhysXDevice.dll.bak').read_bytes(), b'bundled fixture')


if __name__ == '__main__':
    unittest.main()
