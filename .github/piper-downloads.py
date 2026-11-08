import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    headers = {"User-Agent": "Proton-Piper-CI"}
    if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    for attempt in range(5):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as response:
                data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("Piper download exceeds 64 MiB: " + url)
            return data
        except urllib.error.HTTPError as error:
            if not 500 <= error.code <= 599 or attempt == 4:
                raise
            print(f"Piper HTTP {error.code}, retry {attempt + 1}/4: {url}", flush=True)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            if attempt == 4:
                raise
            print(f"Piper transport retry {attempt + 1}/4: {url}", flush=True)
        time.sleep(2 ** attempt)


def check_ref(config):
    expected = config["mutable_pic"]
    actual = json.loads(fetch(expected["ref_api"]))["object"]["sha"]
    if actual != expected["commit"]:
        raise ValueError(f"Piper pic branch changed: expected {expected['commit']}, got {actual}")
    print("Piper pic branch verified " + actual, flush=True)


def archive(cache, entry):
    path = cache / entry["filename"]
    if path.is_symlink():
        raise ValueError("Piper cache symlink: " + str(path))
    data = path.read_bytes() if path.exists() else fetch(entry["url"])
    actual = sha(data)
    if actual != entry["sha256"]:
        raise ValueError(f"Piper {entry['name']} SHA256 changed: expected {entry['sha256']}, got {actual}")
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        Path(temporary.name).replace(path)
    print(f"Piper verified {entry['name']} {actual}", flush=True)


def patch(source, role, cache, config):
    path = source / "CMakeLists.txt"
    original = path.read_bytes()
    if sha(original) != config["cmake_sha256"][role]:
        raise ValueError("Unexpected Piper CMakeLists.txt: " + str(path))
    text = original.decode()
    for entry in config["archives"]:
        if entry["role"] != role:
            continue
        token = 'URL "' + entry["cmake_url"] + '"'
        if text.count(token) != 1:
            raise ValueError("Unexpected Piper URL declaration: " + entry["name"])
        options = '\n        URL_HASH SHA256=' + entry["sha256"]
        options += '\n        DOWNLOAD_DIR "' + cache.as_posix() + '"'
        if entry["name"] == "piper-phonemize":
            options += '\n        PATCH_COMMAND python3 "' + str(Path(__file__).resolve())
            options += '" phonemize "' + cache.as_posix() + '" "<SOURCE_DIR>"'
        text = text.replace(token, token + options)
    path.write_text(text)
    print(f"Piper CI download options: {path}, {sha(original)} -> {sha(text.encode())}", flush=True)


def main():
    role, cache, source = sys.argv[1:]
    cache, source = Path(cache), Path(source).resolve()
    if cache.is_symlink():
        raise ValueError("Piper cache symlink: " + str(cache))
    cache = cache.resolve()
    config = json.loads(Path(__file__).with_suffix(".json").read_text())
    if role == "prepare":
        check_ref(config)  # Check the mutable branch even when every archive is cached.
        cache.mkdir(parents=True, exist_ok=True)
        for entry in config["archives"]:
            archive(cache, entry)
        patch(source, "piper", cache, config)
    elif role == "phonemize":
        for entry in config["archives"]:
            archive(cache, entry)
        patch(source, role, cache, config)
    else:
        raise ValueError("Unknown Piper CI operation: " + role)


if __name__ == "__main__":
    main()
