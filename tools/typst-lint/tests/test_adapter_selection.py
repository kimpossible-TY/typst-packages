"""Fixed adapter selection, capture and audit-child identity boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import syntax_client
import audit_rules


@unittest.skipUnless(syntax_client.EXECUTABLE.is_file(), 'Requires a built syntax adapter')
class AdapterSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-adapter-choice-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in ('syntax_client.py', 'typst_lint.py', 'audit_adapter_worker.py'):
            shutil.copy2(TOOL / name, self.root / name)
        self.debug = self.root / 'syntax-adapter/target/debug/typst-lint-syntax'
        self.release = self.root / 'syntax-adapter/target/release/typst-lint-syntax'
        self.debug.parent.mkdir(parents=True)
        self.release.parent.mkdir(parents=True)
        self.binary = syntax_client.EXECUTABLE
        self.launcher = self.root / 'typst-lint'
        self.launcher.write_text('print("audit-worker-ran")\n')

    def install(self, path):
        shutil.copy2(self.binary, path)
        path.chmod(0o700)

    def python(self, script):
        return subprocess.run([sys.executable, '-c', script], cwd=self.root,
                              capture_output=True, text=True, timeout=20)

    def inventory(self):
        result = self.python('import json,syntax_client as c; print(json.dumps(c.audit_adapter_inventory(),sort_keys=True))')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_preference_and_debug_fallback(self):
        self.install(self.debug)
        debug = self.inventory()
        self.assertEqual(debug['path'], 'syntax-adapter/target/debug/typst-lint-syntax')
        self.install(self.release)
        release = self.inventory()
        self.assertEqual(release['path'], 'syntax-adapter/target/release/typst-lint-syntax')
        self.assertEqual(release['sha256'], hashlib.sha256(self.release.read_bytes()).hexdigest())
        r = self.python('import syntax_client as c; print(len(c.parse(b"$x$").nodes)); c.parse(b"$y$"); c.audit_adapter_inventory()')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.debug.unlink()
        self.assertEqual(self.inventory()['path'], 'syntax-adapter/target/release/typst-lint-syntax')
        self.release.unlink()
        result = self.python('import syntax_client as c; c.parse(b"x")')
        self.assertNotEqual(result.returncode, 0)

    def test_real_debug_build_runs_when_release_is_absent(self):
        if not syntax_client.DEBUG_EXECUTABLE.is_file():
            self.skipTest('Requires a real debug build')
        shutil.copy2(syntax_client.DEBUG_EXECUTABLE, self.debug)
        expected = hashlib.sha256(syntax_client.DEBUG_EXECUTABLE.read_bytes()).hexdigest()
        script = ('import json,syntax_client as c; t=c.parse(b"$x^i$"); '
                  'print(json.dumps(dict(path=c.EXECUTABLE.parent.name,sha=c.adapter_identity()[2],nodes=len(t.nodes))))')
        result = self.python(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        self.assertEqual((actual['path'], actual['sha']), ('debug', expected))
        self.assertGreater(actual['nodes'], 1)

    def test_present_invalid_release_never_falls_back(self):
        self.install(self.debug)
        for mode in ('symlink', 'broken-symlink', 'not-executable', 'directory-symlink', 'broken-directory-symlink'):
            with self.subTest(mode=mode):
                if mode == 'symlink':
                    self.release.symlink_to(self.debug)
                elif mode == 'broken-symlink':
                    self.release.symlink_to(self.root / 'missing')
                elif mode == 'not-executable':
                    self.install(self.release)
                    self.release.chmod(0o600)
                elif mode == 'directory-symlink':
                    self.release.parent.rmdir()
                    self.release.parent.symlink_to(self.debug.parent, target_is_directory=True)
                else:
                    self.release.parent.rmdir()
                    self.release.parent.symlink_to(self.root / 'missing-directory', target_is_directory=True)
                r = self.python('import syntax_client as c; c.parse(b"x")')
                self.assertNotEqual(r.returncode, 0, mode)
                audit = self.python('import syntax_client as c; c.audit_adapter_inventory()')
                self.assertNotEqual(audit.returncode, 0, mode)
                if mode in ('directory-symlink', 'broken-directory-symlink'):
                    self.release.parent.unlink()
                    self.release.parent.mkdir()
                else:
                    self.release.unlink()

    def test_binary_mutation_during_parse_or_between_calls_is_rejected(self):
        self.install(self.release)
        script = '''import syntax_client as c
old = c._run
def changed(*args):
    result = old(*args)
    with c.EXECUTABLE.open('ab') as f: f.write(b'changed')
    return result
c._run = changed
c.parse(b"x")
'''
        r = self.python(script)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Syntax adapter changed', r.stderr)
        self.release.unlink()
        self.install(self.release)
        r = self.python('import syntax_client as c; c.parse(b"x"); c.EXECUTABLE.write_bytes(c.EXECUTABLE.read_bytes()+b"x"); c.parse(b"x")')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Syntax adapter changed', r.stderr)

    def test_release_ancestor_symlinks_are_not_mistaken_for_absence(self):
        self.install(self.debug)
        for component in (self.debug.parents[1], self.debug.parents[2]):
            for broken in (False, True):
                with self.subTest(component=component.name, broken=broken):
                    moved = component.with_name(component.name + '-real')
                    component.rename(moved)
                    try:
                        component.symlink_to(self.root / 'missing' if broken else moved,
                                             target_is_directory=True)
                        for statement in ('c.parse(b"x")', 'c.audit_adapter_inventory()'):
                            r = self.python('import syntax_client as c; ' + statement)
                            self.assertNotEqual(r.returncode, 0, (component, broken, statement))
                    finally:
                        component.unlink()
                        moved.rename(component)

    def test_release_access_error_does_not_trigger_debug_fallback(self):
        self.install(self.debug)
        script = '''from pathlib import Path
original = Path.stat
def denied(self, *args, **kwargs):
    if self.name == 'release':
        raise PermissionError('fixture access denied')
    return original(self, *args, **kwargs)
Path.stat = denied
import syntax_client as c
print(c.EXECUTABLE.parent.name, flush=True)
c.parse(b"x")
'''
        r = self.python(script)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), 'release')

    def test_debug_running_mutation_and_release_appearance_are_rejected(self):
        self.install(self.debug)
        for operation in ('mutation', 'release', 'transient-release'):
            with self.subTest(operation=operation):
                script = '''import pathlib, shutil, syntax_client as c
old = c._run
def changed(*args):
    result = old(*args)
    if OP == 'mutation':
        with c.EXECUTABLE.open('ab') as f: f.write(b'changed')
    else:
        shutil.copyfile(c.DEBUG_EXECUTABLE, c.RELEASE_EXECUTABLE)
        if OP == 'transient-release':
            c.RELEASE_EXECUTABLE.unlink()
    return result
c._run = changed
c.parse(b"x")
'''.replace('OP', repr(operation))
                r = self.python(script)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('Syntax adapter', r.stderr)
                if self.release.exists():
                    self.release.unlink()
                self.debug.unlink()
                self.install(self.debug)

    def test_directory_swap_and_restore_during_parse_is_rejected(self):
        self.install(self.debug)
        script = '''import pathlib,shlex,syntax_client as c
old = c._run
def changed(*args):
    directory = c.DEBUG_EXECUTABLE.parent
    saved = directory.with_name('saved-debug')
    swapped = directory.with_name('swapped-debug')
    marker = directory.parents[2] / 'executed-marker'
    directory.rename(saved)
    directory.mkdir()
    replacement = directory / 'typst-lint-syntax'
    replacement.write_text('#!/bin/sh\\nprintf executed > ' + shlex.quote(str(marker)) +
                           '\\nexec ' + shlex.quote(str(saved / 'typst-lint-syntax')) + ' "$@"\\n')
    replacement.chmod(0o700)
    try:
        return old(*args)
    finally:
        directory.rename(swapped)
        saved.rename(directory)
c._run = changed
c.parse(b"x")
'''
        r = self.python(script)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Syntax adapter changed', r.stderr)
        self.assertTrue((self.root / 'executed-marker').is_file())

    def test_release_running_transient_debug_directory_change_is_rejected(self):
        self.install(self.release)
        script = '''import syntax_client as c
old = c._run
def changed(*args):
    result = old(*args)
    other = c.DEBUG_EXECUTABLE.parent / 'temporary-artifact'
    other.write_bytes(b'x')
    other.unlink()
    return result
c._run = changed
c.parse(b"x")
'''
        r = self.python(script)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Syntax adapter changed', r.stderr)

    def test_audit_child_refuses_mismatched_selection_and_binary(self):
        self.install(self.debug)
        before = self.inventory()
        self.install(self.release)
        cmd = [sys.executable, str(self.root / 'audit_adapter_worker.py'), json.dumps(before), str(self.launcher), 'x']
        r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('audit-worker-ran', r.stdout)
        now = self.inventory()
        self.release.write_bytes(self.release.read_bytes() + b'changed')
        cmd[2] = json.dumps(now)
        r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('audit-worker-ran', r.stdout)

    def test_audit_child_rejects_selection_change_during_cli_execution(self):
        self.install(self.debug)
        before = self.inventory()
        self.launcher.write_text('import pathlib,shutil\n'
            f'shutil.copyfile({str(self.debug)!r}, {str(self.release)!r})\n'
            'print("child-produced-output")\n')
        cmd = [sys.executable, str(self.root / 'audit_adapter_worker.py'), json.dumps(before), str(self.launcher), 'x']
        r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Syntax adapter selection changed', r.stderr)

    def test_audit_child_rejects_transient_release_change(self):
        self.install(self.debug)
        before = self.inventory()
        self.launcher.write_text('import pathlib,shutil\n'
            f'shutil.copyfile({str(self.debug)!r}, {str(self.release)!r})\n'
            f'pathlib.Path({str(self.release)!r}).unlink()\n'
            'print("child-produced-output")\n')
        cmd = [sys.executable, str(self.root / 'audit_adapter_worker.py'), json.dumps(before), str(self.launcher), 'x']
        r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Syntax adapter changed', r.stderr)

    def test_unselected_nonexecutable_debug_does_not_block_release_audit(self):
        self.install(self.debug)
        self.debug.chmod(0o600)
        self.install(self.release)
        inventory = self.inventory()
        self.assertIsNotNone(inventory['sha256'])
        self.assertEqual(inventory['candidates']['debug']['kind'], 'regular')

    def test_audit_report_exposes_selected_hash_without_host_identity(self):
        report = audit_rules.snapshot()
        self.assertEqual(report['adapter_path'], syntax_client.EXECUTABLE.relative_to(TOOL).as_posix())
        self.assertEqual(report['adapter_sha256'], hashlib.sha256(syntax_client.EXECUTABLE.read_bytes()).hexdigest())
        self.assertFalse(report['adapter_path'].startswith('/'))
        for state in report['adapter_candidates'].values():
            if state is not None:
                self.assertNotIn('identity', state)

    def test_audit_worker_and_normal_cli_reports_match(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'main.typ').write_text('$x^i$\n')
        (project / '.typst-lint.toml').write_text('rule-set="pde"\ninclude=["**/*.typ"]\n')
        args = [str(TOOL / 'typst-lint'), 'check', '--root', str(project), '--package-path',
                str(TOOL.parents[1]), '--format', 'json']
        expected = syntax_client.audit_adapter_inventory()
        direct = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=30)
        worker = subprocess.run([sys.executable, str(TOOL / 'audit_adapter_worker.py'),
                                 json.dumps(expected, sort_keys=True), *args],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(worker.returncode, direct.returncode, worker.stderr)
        self.assertEqual(json.loads(worker.stdout), json.loads(direct.stdout))


if __name__ == '__main__':
    unittest.main()
