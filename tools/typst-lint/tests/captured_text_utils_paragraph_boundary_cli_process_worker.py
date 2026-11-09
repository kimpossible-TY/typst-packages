"""Owned boundary process driver with one compiler-dependency refusal mode.

Reuse the reviewed test-only process/barrier protocol, not production hooks.
No runtime API, command-line flag or installed compiler is modified.
"""
import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import captured_text_utils_paragraph_cli_process_worker as driver

_original_make = driver.make_compiler


def make_compiler(request):
    artifact = _original_make(request)
    if request.get('compiler_mode') != 'refuse-dependencies':
        return artifact
    path = artifact.path
    old = b"observed = (leaf / 'typst.toml', source.relative_to(pathlib.Path.cwd()), leaf / 'lib.typ')"
    new = b"observed = (source.relative_to(pathlib.Path.cwd()), leaf / 'lib.typ')"
    raw = path.read_bytes()
    assert raw.count(old) == 1
    raw = raw.replace(old, new)
    path.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o500)
    return driver.runtime._ReviewedNativeArtifact(path, hashlib.sha256(raw).hexdigest(), driver.runtime._VERSION)


driver.make_compiler = make_compiler

if __name__ == '__main__':
    driver.main()
