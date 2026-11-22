"""Real process death/restart tests, exclusively against temporary consumers.

These test process-crash recovery, not power-loss durability or a Typst analyzer.
Each operation uses a fresh interpreter; the parent kills a blocked child with
SIGKILL after a deterministic protocol boundary, without Python unwinding.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest

WORKER = Path(__file__).with_name('transaction_process_worker.py')


@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX storage protocol')
class ProcessCrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.originals = {'a.typ': b'// existing dirty user edit\nbad KEEP\n',
                          'b.typ': b'bad\n'}
        for name, content in self.originals.items():
            (self.root / name).write_bytes(content)
        self.untouched = self.root / 'unselected.typ'
        self.untouched.write_bytes(b'bad unselected user work\n')
        self.untouched_state = self.state(self.untouched)

    def state(self, path):
        value = path.stat()
        return path.read_bytes(), value.st_ino, value.st_mode

    def request(self, action, **kwargs):
        return dict(root=str(self.root), store=str(self.store), action=action, **kwargs)

    def run_worker(self, action, **kwargs):
        result = subprocess.run([sys.executable, str(WORKER), json.dumps(self.request(action, **kwargs))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def kill_worker(self, action, boundary, **kwargs):
        child = subprocess.Popen(
            [sys.executable, str(WORKER), json.dumps(self.request(action, boundary=boundary, **kwargs))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=20), 'Child did not reach crash boundary')
                line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail(f'Child exited before crash boundary: {error}')
            self.assertEqual(json.loads(line), {'paused': list(boundary)})
            # Kill the exact child we own, not a name/pattern or external process.
            child.kill()
            _, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def pending(self):
        result = self.run_worker('probe')
        self.assertEqual(set(result), {'pending'}, result)
        self.assertEqual(len(result['pending']), 1)
        return result['pending'][0]

    def assert_originals(self):
        for name, data in self.originals.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual(self.state(self.untouched), self.untouched_state)

    def assert_recovery_complete(self, journal):
        report = self.run_worker('recover', journal=journal)
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['errors'], [])
        self.assert_originals()
        self.assertEqual(self.run_worker('probe'), {'open': True})
        # Repeat recovery in yet another interpreter: it must be a no-op.
        before = {p: self.state(self.root / p) for p in self.originals}
        self.assertTrue(self.run_worker('recover', journal=journal)['complete'])
        self.assertEqual({p: self.state(self.root / p) for p in self.originals}, before)
        self.assertEqual(self.run_worker('apply')['modified'], ['a.typ', 'b.typ'])
        return report

    def test_sigkill_before_first_rename_releases_lock_and_recovers_unchanged(self):
        self.kill_worker('apply', ('before_replace', 'a.typ'))
        self.assert_originals()
        report = self.assert_recovery_complete(self.pending())
        self.assertEqual(report['restored'], [])
        self.assertEqual(report['unchanged'], ['a.typ', 'b.typ'])

    def test_sigkill_after_first_rename_before_progress_restores_partial_apply(self):
        self.kill_worker('apply', ('after_replace', 'a.typ'))
        self.assertIn(b'good', (self.root / 'a.typ').read_bytes())
        self.assertEqual((self.root / 'b.typ').read_bytes(), self.originals['b.typ'])
        report = self.assert_recovery_complete(self.pending())
        self.assertEqual(report['restored'], ['a.typ'])

    def test_sigkill_after_second_rename_before_progress_restores_both_files(self):
        self.kill_worker('apply', ('after_replace', 'b.typ'))
        for name in self.originals:
            self.assertIn(b'good', (self.root / name).read_bytes())
        report = self.assert_recovery_complete(self.pending())
        self.assertEqual(report['restored'], ['a.typ', 'b.typ'])

    def test_sigkill_during_recovery_can_restart_without_replacing_restored_inode(self):
        self.kill_worker('apply', ('after_replace', 'b.typ'))
        journal = self.pending()
        self.kill_worker('recover', ('after_restore', 'a.typ'), journal=journal)
        restored = self.state(self.root / 'a.typ')
        self.assertEqual(restored[0], self.originals['a.typ'])
        self.assertEqual(self.pending(), journal)
        report = self.run_worker('recover', journal=journal)
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['unchanged'], ['a.typ'])
        self.assertEqual(report['restored'], ['b.typ'])
        self.assertEqual(self.state(self.root / 'a.typ'), restored)
        self.assert_originals()
        self.assertEqual(self.run_worker('probe'), {'open': True})

    def test_sigkill_then_later_edit_requires_current_exact_conflict_acknowledgment(self):
        self.kill_worker('apply', ('after_replace', 'b.typ'))
        journal = self.pending()
        a = self.root / 'a.typ'
        a.write_bytes(b'later user work\n')
        first = self.state(a)
        report = self.run_worker('recover', journal=journal)
        self.assertFalse(report['complete'])
        self.assertEqual(report['changed'], ['a.typ'])
        self.assertEqual(report['restored'], ['b.typ'])
        self.assertEqual(self.state(a), first)
        self.assertEqual(self.pending(), journal)
        a.write_bytes(b'even newer user work\n')
        latest = self.state(a)
        stale = self.run_worker('recover', journal=journal, preserve=report['conflicts'])
        self.assertFalse(stale['complete'])
        self.assertEqual(stale['unresolved'], ['a.typ'])
        self.assertEqual(self.state(a), latest)
        self.assertEqual(self.pending(), journal)
        resolved = self.run_worker('recover', journal=journal, preserve=stale['conflicts'])
        self.assertTrue(resolved['complete'], resolved)
        self.assertEqual(resolved['preserved'], ['a.typ'])
        self.assertEqual(self.state(a), latest)
        self.assertEqual((self.root / 'b.typ').read_bytes(), self.originals['b.typ'])
        self.assertEqual(self.state(self.untouched), self.untouched_state)
        self.assertEqual(self.run_worker('probe'), {'open': True})

    def test_sigkill_then_same_postimage_on_new_inode_is_preserved_as_conflict(self):
        self.kill_worker('apply', ('after_replace', 'a.typ'))
        journal = self.pending()
        a = self.root / 'a.typ'
        old_inode = a.stat().st_ino
        replacement = self.root / 'user-save.tmp'
        replacement.write_bytes(a.read_bytes())
        os.replace(replacement, a)
        self.assertNotEqual(a.stat().st_ino, old_inode)
        saved = self.state(a)
        report = self.run_worker('recover', journal=journal)
        self.assertFalse(report['complete'])
        self.assertEqual(report['changed'], ['a.typ'])
        self.assertEqual(self.state(a), saved)
        self.assertEqual(self.pending(), journal)
        self.assertTrue(self.run_worker('recover', journal=journal,
                                       preserve=report['conflicts'])['complete'])
        self.assertEqual(self.state(a), saved)
        self.assertEqual(self.run_worker('probe'), {'open': True})


if __name__ == '__main__':
    unittest.main()
