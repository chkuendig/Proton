import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error


spec = importlib.util.spec_from_file_location("piper", Path(__file__).with_name("piper-downloads.py"))
piper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(piper)


class Downloads(unittest.TestCase):
    def test_5xx_retries_with_bounded_backoff(self):
        error = urllib.error.HTTPError("https://example.test/a", 504, "timeout", {}, None)
        with mock.patch.object(piper.urllib.request, "urlopen", side_effect=[error, error, io.BytesIO(b"ok")]) as request, mock.patch.object(piper.time, "sleep") as sleep:
            self.assertEqual(piper.fetch("https://example.test/a"), b"ok")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_retry_exhaustion_and_4xx_fail_fast(self):
        for status, calls in [(503, 5), (404, 1)]:
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://example.test/a", status, "failed", {}, None)
                with mock.patch.object(piper.urllib.request, "urlopen", side_effect=error) as request, mock.patch.object(piper.time, "sleep") as sleep:
                    with self.assertRaises(urllib.error.HTTPError):
                        piper.fetch("https://example.test/a")
                self.assertEqual(request.call_count, calls)
                self.assertEqual(sleep.call_count, calls - 1)

    def test_interrupted_response_reads_retry(self):
        for error in [ConnectionResetError(), piper.http.client.IncompleteRead(b"partial", 10), piper.http.client.RemoteDisconnected()]:
            with self.subTest(error=type(error).__name__):
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.side_effect = error
                with mock.patch.object(piper.urllib.request, "urlopen", side_effect=[response, io.BytesIO(b"complete")]), mock.patch.object(piper.time, "sleep") as sleep:
                    self.assertEqual(piper.fetch("https://example.test/a"), b"complete")
                sleep.assert_called_once_with(1)

    def test_cached_archive_requires_hash_and_regular_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            entry = dict(name="test", filename="archive.zip", url="https://example.test/a", sha256=piper.sha(b"archive"))
            with mock.patch.object(piper, "fetch", return_value=b"archive") as fetch:
                piper.archive(cache, entry)
                piper.archive(cache, entry)
                self.assertEqual(fetch.call_count, 1)
                (cache / entry["filename"]).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "SHA256 changed"):
                    piper.archive(cache, entry)
                self.assertEqual(fetch.call_count, 1)
            (cache / "link.zip").symlink_to(cache / entry["filename"])
            with self.assertRaisesRegex(ValueError, "symlink"):
                piper.archive(cache, dict(entry, filename="link.zip"))

    def test_mutable_branch_drift_fails_before_any_cached_use(self):
        config = dict(mutable_pic=dict(ref_api="https://api.github.com/ref", commit="expected"))
        with mock.patch.object(piper, "fetch", return_value=b'{"object":{"sha":"changed"}}'):
            with self.assertRaisesRegex(ValueError, "pic branch changed"):
                piper.check_ref(config)

    def test_overlay_preserves_source_except_download_options(self):
        config = json.loads(Path(__file__).with_name("piper-downloads.json").read_text())
        for role in ["piper", "phonemize"]:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                original = "project(public_test)\n" + "\n".join('URL "' + x["cmake_url"] + '"' for x in config["archives"] if x["role"] == role) + "\nCMAKE_ARGS -DPRESERVE=ON\n"
                config["cmake_sha256"][role] = piper.sha(original.encode())
                (source / "CMakeLists.txt").write_text(original)
                piper.patch(source, role, source / "cache", config)
                text = (source / "CMakeLists.txt").read_text()
                self.assertEqual("\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("URL_HASH ", "DOWNLOAD_DIR ", "PATCH_COMMAND "))) + "\n", original)
                for entry in config["archives"]:
                    if entry["role"] == role:
                        self.assertIn("URL_HASH SHA256=" + entry["sha256"], text)
                self.assertEqual(text.count("PATCH_COMMAND"), int(role == "piper"))
                with self.assertRaisesRegex(ValueError, "Unexpected Piper CMakeLists"):
                    piper.patch(source, role, source / "cache", config)


if __name__ == "__main__":
    unittest.main()
