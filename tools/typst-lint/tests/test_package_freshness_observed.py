"""Read-only, bounded comparisons for explicitly observed package files."""
from dataclasses import asdict
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_freshness_observed as observed
from typst_lint import InputError, SafetyError


class ObservedFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-observed-packages-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.checkout, self.installed = (self.base / name for name in ('checkout', 'installed'))
        self.checkout.mkdir()
        self.installed.mkdir()

    def write(self, root, path, data):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def compare(self, paths):
        return observed.compare_observed(str(self.checkout), str(self.installed), paths)

    def test_matches_differences_and_missing_files_are_independent(self):
        base = 'local/example/1.0.0/'
        binary = b'\x00\xff\x80'
        self.write(self.checkout, base + 'same.bin', binary)
        self.write(self.installed, base + 'same.bin', binary)
        self.write(self.checkout, base + 'changed.typ', b'checkout')
        self.write(self.installed, base + 'changed.typ', b'installed')
        self.write(self.checkout, base + 'only-checkout.typ', b'x')
        self.write(self.installed, base + 'only-installed.typ', b'y')
        paths = [base + name for name in ('same.bin', 'changed.typ', 'only-checkout.typ',
                                         'only-installed.typ', 'neither.typ')]
        report = self.compare(paths)
        self.assertEqual([row.status for row in report.files],
                         ['match', 'different', 'installed-missing', 'checkout-missing', 'both-missing'])
        self.assertEqual(report.files[0].checkout_sha256, hashlib.sha256(binary).hexdigest())
        self.assertEqual(report.files[0].checkout_sha256, report.files[0].installed_sha256)
        self.assertNotEqual(report.files[1].checkout_sha256, report.files[1].installed_sha256)
        self.assertFalse(report.complete_package_equality_proved or report.runtime_selection_proved
                         or report.commit_provenance_proved or report.upstream_freshness_proved
                         or report.observation_origin_proved or report.filesystem_lease)
        self.assertNotIn(binary, repr(asdict(report)).encode())
        self.assertEqual((self.checkout / paths[0]).read_bytes(), binary)
        self.assertEqual((self.installed / paths[1]).read_bytes(), b'installed')

    def test_symlink_and_nonregular_file_are_unavailable_not_missing(self):
        path = 'local/example/1.0.0/lib.typ'
        self.write(self.checkout, path, b'normal')
        destination = self.installed / path
        destination.parent.mkdir(parents=True)
        destination.symlink_to(self.checkout / path)
        report = self.compare([path])
        self.assertEqual(report.files[0].status, 'unavailable')
        self.assertIsNone(report.files[0].checkout_sha256)
        destination.unlink()
        destination.mkdir()
        self.assertEqual(self.compare([path]).files[0].status, 'unavailable')

    def test_changed_file_is_unavailable_after_batch_capture(self):
        path = 'local/example/1.0.0/lib.typ'
        self.write(self.checkout, path, b'old')
        installed = self.write(self.installed, path, b'old')
        original = observed.runtime._same
        changed = False
        def mutate(target, evidence, bound, **kwargs):
            nonlocal changed
            if not changed:
                installed.write_bytes(b'new')
                changed = True
            return original(target, evidence, bound, **kwargs)
        with patch.object(observed.runtime, '_same', side_effect=mutate):
            report = self.compare([path])
        self.assertEqual(report.files[0].status, 'unavailable')
        self.assertIsNone(report.files[0].installed_sha256)

    def test_absent_then_created_file_is_unavailable(self):
        path = 'local/example/1.0.0/lib.typ'
        self.write(self.checkout, path, b'content')
        self.installed.joinpath('local/example/1.0.0').mkdir(parents=True)
        original = observed._capture
        calls = 0
        def create_on_recheck(target):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.write(self.installed, path, b'content')
            return original(target)
        with patch.object(observed, '_capture', side_effect=create_on_recheck):
            report = self.compare([path])
        self.assertEqual(report.files[0].status, 'unavailable')

    def test_paths_roots_and_budgets_are_restricted(self):
        for path in ('../escape', '/absolute', 'preview/example/1.0.0/lib.typ',
                     'local/../example/1.0.0/lib.typ', 'local/example/1.0.0/a\nb'):
            with self.subTest(path=path), self.assertRaises((InputError, SafetyError)):
                self.compare([path])
        with self.assertRaises((InputError, SafetyError)):
            self.compare(['local/example/1.0.0/Foo', 'local/example/1.0.0/foo'])
        with self.assertRaises((InputError, SafetyError)):
            self.compare(['local/Example/1.0.0/a', 'local/example/1.0.0/b'])
        with self.assertRaises((InputError, SafetyError)):
            observed.compare_observed(str(self.checkout) + '/..', str(self.installed), [])
        root_link = self.base / 'root-link'
        root_link.symlink_to(self.checkout, target_is_directory=True)
        with self.assertRaises(SafetyError):
            observed.compare_observed(str(root_link), str(self.installed), [])
        path = 'local/example/1.0.0/lib.typ'
        self.write(self.checkout, path, b'1234')
        self.write(self.installed, path, b'1234')
        with patch.object(observed, 'MAX_TOTAL_BYTES', 7), self.assertRaises(InputError):
            self.compare([path])

    def test_empty_observation_does_not_prove_anything(self):
        report = self.compare([])
        self.assertEqual(report.files, ())
        self.assertFalse(report.complete_package_equality_proved or report.upstream_freshness_proved)
