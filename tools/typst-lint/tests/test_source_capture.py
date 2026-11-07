"""Selected-source snapshots and race fixtures confined to test-owned files."""
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_loader
import syntax_client
import typst_lint as lint


class SourceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-source-capture-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.target = self.root / 'main.typ'

    def put(self, name, raw=b'Ordinary prose.\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def capture(self, config=None):
        return lint.capture_selected_sources(self.root, config or lint.Config())

    def guarded_source_read(self):
        original = Path.read_bytes
        def read(path):
            if path == self.target:
                self.fail('Selected bytes must not use an unchecked pathname read')
            return original(path)
        return patch.object(Path, 'read_bytes', read)

    def unavailable_parser(self):
        return patch.object(syntax_client, 'parse', side_effect=syntax_client.AdapterError('test parser unavailable'))

    def assert_unsupported(self, value, reason):
        self.assertFalse(value['diagnostics'] or value['proposed_edits'] or value['compiler_invoked'])
        self.assertTrue(any(reason in message for row in value['coverage']['files']
                            for message in row['reasons']))

    def test_capture_is_frozen_and_metadata_preserves_original_bytes(self):
        raw = b'\xef\xbb\xbf// comment\r\n' + '한글'.encode() + b'\r\n'
        self.put('main.typ', raw)
        with self.guarded_source_read():
            selected = self.capture()
            records = lint.collect(self.root, lint.Config())
        self.assertIsInstance(selected, tuple)
        self.assertEqual(selected[0].source, raw)
        self.assertEqual(selected[0].sha256, lint.digest(raw))
        self.assertEqual(records, [dict(path='main.typ', sha256=lint.digest(raw), bytes=len(raw))])
        self.assertEqual(selected[0].record(), records[0])
        self.assertNotIn('source=', repr(selected[0]))
        for field in ('source', 'sha256', 'path'):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(selected[0], field, 'changed')
        self.target.write_bytes(b'changed later')
        self.assertEqual(selected[0].source, raw)
        self.assertEqual(selected[0].sha256, lint.digest(raw))
        records[0]['sha256'] = 'changed projection'
        self.assertEqual(selected[0].record()['sha256'], lint.digest(raw))
        with self.assertRaises(lint.InputError):
            lint._SelectedSource('main.typ', bytearray(raw))

    def test_selection_exclusions_and_global_order_are_unchanged(self):
        self.put('z.typ', b'z')
        self.put('a/b.typ', b'b')
        self.put('a.typ', b'a')
        self.put('notes.md', b'n')
        self.put('generated/invalid.typ', b'\xff')
        self.put('ignored.txt', b'\xff')
        (self.root / 'vendor').symlink_to(self.base, target_is_directory=True)
        config = lint.Config(include=('**/*.typ', '*.md'), exclude=('generated/**', 'vendor/**'))
        with patch.object(lint, 'MAX_CAPTURE_BYTES', 4):
            selected = self.capture(config)
        self.assertEqual([item.path for item in selected], ['a.typ', 'a/b.typ', 'notes.md', 'z.typ'])
        self.assertEqual([item.record() for item in selected], lint.collect(self.root, config))
        with self.assertRaisesRegex(lint.InputError, 'Symbolic-link directory'):
            self.capture()

    def test_each_supported_suffix_has_the_actual_parser_input_bound(self):
        for suffix in ('.typ', '.md'):
            with self.subTest(suffix=suffix):
                path = self.put('limit' + suffix, b'x' * syntax_client.MAX_INPUT)
                config = lint.Config(include=('*' + suffix,))
                selected = self.capture(config)
                self.assertEqual(len(selected[0].source), syntax_client.MAX_INPUT)
                path.write_bytes(b'x' * (syntax_client.MAX_INPUT + 1))
                with self.assertRaisesRegex(lint.InputError, 'exceeds byte limit'):
                    self.capture(config)

    def test_aggregate_capture_accepts_exact_limit_and_never_truncates(self):
        self.assertEqual(lint.MAX_CAPTURE_BYTES, 64 * 1024 * 1024)
        self.put('a.typ', b'aaa')
        self.put('b.md', b'bb')
        config = lint.Config(include=('*.typ', '*.md'))
        with patch.object(lint, 'MAX_CAPTURE_BYTES', 5):
            self.assertEqual([item.source for item in self.capture(config)], [b'aaa', b'bb'])
            self.put('empty.typ', b'')
            self.assertEqual(len(self.capture(config)), 3)
            self.put('c.typ', b'c')
            with self.assertRaisesRegex(lint.InputError, r'aggregate capture byte limit \(5\): c.typ'):
                self.capture(config)

    def test_invalid_utf8_has_explicit_input_error(self):
        for suffix in ('.typ', '.md'):
            with self.subTest(suffix=suffix):
                self.put('invalid' + suffix, b'\xff')
                with self.assertRaisesRegex(lint.InputError, 'UTF-8'):
                    self.capture(lint.Config(include=('*' + suffix,)))

    def test_capture_rejects_postcheck_identical_byte_symlink_without_reading_target(self):
        raw = b'identical owned fixture'
        self.put('main.typ', raw)
        outside = self.base / 'outside.typ'
        outside.write_bytes(raw)
        original = lint.checked_path
        def raced(root, name):
            result = original(root, name)
            self.target.unlink()
            self.target.symlink_to(outside)
            return result
        with patch.object(lint, 'checked_path', side_effect=raced), self.guarded_source_read():
            with self.assertRaisesRegex(lint.InputError, 'symbolic link'):
                self.capture()
        self.assertEqual(outside.read_bytes(), raw)

    def test_capture_rejects_postcheck_ancestor_symlink(self):
        self.put('nested/main.typ', b'owned fixture')
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'main.typ').write_bytes(b'owned fixture')
        original = lint.checked_path
        def raced(root, name):
            result = original(root, name)
            (self.root / 'nested').rename(self.root / 'retained')
            (self.root / 'nested').symlink_to(outside, target_is_directory=True)
            return result
        with patch.object(lint, 'checked_path', side_effect=raced):
            with self.assertRaisesRegex(lint.InputError, 'not a directory or is a symbolic link'):
                self.capture()
        self.assertEqual((outside / 'main.typ').read_bytes(), b'owned fixture')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Requires POSIX FIFO')
    def test_capture_rejects_postcheck_fifo_before_open(self):
        self.put('main.typ')
        original_check, original_open = lint.checked_path, os.open
        swapped = False
        def raced(root, name):
            nonlocal swapped
            result = original_check(root, name)
            self.target.unlink()
            os.mkfifo(self.target)
            swapped = True
            return result
        def opening(name, flags, *args, **kwargs):
            if swapped and name in (self.target, 'main.typ'):
                self.fail('The already-visible FIFO must be rejected before open')
            return original_open(name, flags, *args, **kwargs)
        with patch.object(lint, 'checked_path', side_effect=raced), \
                patch.object(os, 'open', side_effect=opening), self.guarded_source_read():
            with self.assertRaisesRegex(lint.InputError, 'not a regular file'):
                self.capture()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Requires POSIX FIFO')
    def test_fifo_swap_between_snapshot_stat_and_open_uses_nonblock(self):
        self.put('main.typ')
        original_open = os.open
        swapped = False
        def opening(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == 'main.typ' and not swapped:
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_NONBLOCK)
                self.target.unlink()
                os.mkfifo(self.target)
                swapped = True
            return original_open(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), self.guarded_source_read():
            with self.assertRaisesRegex(lint.InputError, 'changed while opening'):
                self.capture()
        self.assertTrue(swapped)

    def test_hardlink_swap_between_snapshot_stat_and_open_is_rejected(self):
        raw = b'owned hardlink fixture'
        self.put('main.typ', raw)
        outside = self.base / 'outside.typ'
        outside.write_bytes(raw)
        original_open = os.open
        swapped = False
        def opening(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == 'main.typ' and not swapped:
                self.target.unlink()
                os.link(outside, self.target)
                swapped = True
            return original_open(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), self.guarded_source_read():
            with self.assertRaisesRegex(lint.InputError, 'changed while opening'):
                self.capture()
        self.assertTrue(swapped)
        self.assertEqual(outside.read_bytes(), raw)

    def test_stream_growth_after_open_is_bounded(self):
        self.put('main.typ', b'x')
        original_read = package_loader._Snapshot._read_bytes
        grown = False
        def reading(fd, limit, path):
            nonlocal grown
            if path == self.target and not grown:
                grown = True
                self.target.write_bytes(b'x' * (syntax_client.MAX_INPUT + 1))
            return original_read(fd, limit, path)
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=reading):
            with self.assertRaisesRegex(lint.InputError, 'exceeds byte limit'):
                self.capture()
        self.assertTrue(grown)

    def test_same_size_mutation_is_caught_by_retained_descriptor_bytes(self):
        self.put('main.typ', b'original')
        original_read = package_loader._Snapshot._read_bytes
        changed = False
        def reading(fd, limit, path):
            nonlocal changed
            raw = original_read(fd, limit, path)
            if path == self.target and not changed:
                changed = True
                self.target.write_bytes(b'modified')
            return raw
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=reading), \
                patch.object(package_loader, '_file_signature', return_value=()):
            with self.assertRaisesRegex(lint.InputError, 'content changed'):
                self.capture()
        self.assertTrue(changed)

    def test_identical_byte_inode_replacement_during_read_is_rejected(self):
        self.put('main.typ', b'owned fixture')
        replacement = self.base / 'replacement.typ'
        replacement.write_bytes(b'owned fixture')
        original_read = package_loader._Snapshot._read_bytes
        def reading(fd, limit, path):
            raw = original_read(fd, limit, path)
            if path == self.target and replacement.exists():
                os.replace(replacement, self.target)
            return raw
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=reading):
            with self.assertRaisesRegex(lint.InputError, 'changed while reading'):
                self.capture()

    def test_ancestor_replacement_with_identical_file_during_read_is_rejected(self):
        target = self.put('nested/main.typ', b'owned fixture')
        original_read = package_loader._Snapshot._read_bytes
        changed = False
        def reading(fd, limit, path):
            nonlocal changed
            raw = original_read(fd, limit, path)
            if path == target and not changed:
                changed = True
                (self.root / 'nested').rename(self.root / 'retained')
                self.put('nested/main.typ', raw)
            return raw
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=reading):
            with self.assertRaisesRegex(lint.InputError, 'directory changed'):
                self.capture()
        self.assertTrue(changed)

    def test_capture_verification_rereads_the_same_descriptor(self):
        self.put('main.typ')
        original_read = package_loader._Snapshot._read_bytes
        reads = []
        def reading(fd, limit, path):
            reads.append((fd, limit, path))
            return original_read(fd, limit, path)
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=reading), self.guarded_source_read():
            self.capture()
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[0], reads[1])
        self.assertEqual(reads[0][1], syntax_client.MAX_INPUT)

    def test_report_keeps_json_records_and_markdown_coverage_without_exposing_bytes(self):
        raw = b'\xef\xbb\xbfOrdinary prose.\r\n'
        self.put('main.typ', raw)
        self.put('notes.md', b'# Notes\r\n')
        self.put('.typst-lint.toml', b'include=["*.typ","*.md"]\n')
        records = lint.collect(self.root, lint.read_config(self.root, lint.load_rules()[0]['rules']))
        with self.unavailable_parser(), self.guarded_source_read():
            value = lint.report(self.root, 'check')
        self.assertEqual(value['inputs'], records)
        self.assertEqual(value['inputs'][0], dict(path='main.typ', sha256=lint.digest(raw), bytes=len(raw)))
        self.assertTrue(all(set(item) == {'path', 'sha256', 'bytes'} for item in value['inputs']))
        self.assertTrue(any(row['path'] == 'notes.md' and row['reasons'] == ['Markdown extraction is not implemented']
                            for row in value['coverage']['files']))
        self.assertIsInstance(json.loads(json.dumps(value)), dict)
        self.assertFalse(value['compiler_invoked'] or value['applied_fixes'])

    def test_report_parses_the_captured_object_not_the_reread_buffer(self):
        self.put('main.typ')
        original_capture, original_read = lint.capture_selected_sources, lint._read_selected_source
        captured, rereads = [], []
        def capturing(root, config):
            records = original_capture(root, config)
            captured.extend(records)
            return records
        def reading(root, name, *, reread=False):
            raw = original_read(root, name, reread=reread)
            if reread:
                raw = bytes(bytearray(raw))
                rereads.append(raw)
            return raw
        with patch.object(lint, 'capture_selected_sources', side_effect=capturing), \
                patch.object(lint, '_read_selected_source', side_effect=reading), \
                self.unavailable_parser() as parse:
            lint.report(self.root, 'check')
        self.assertEqual(parse.call_count, 1)
        self.assertIs(parse.call_args.args[0], captured[0].source)
        self.assertIsNot(parse.call_args.args[0], rereads[0])

    def test_nonentrypoint_mutation_after_capture_is_unsupported_before_parse(self):
        self.put('main.typ', b'original')
        original_capture = lint.capture_selected_sources
        def capturing(root, config):
            records = original_capture(root, config)
            self.target.write_bytes(b'modified')
            return records
        with patch.object(lint, 'capture_selected_sources', side_effect=capturing), \
                self.unavailable_parser() as parse, self.guarded_source_read():
            value = lint.report(self.root, 'check')
        parse.assert_not_called()
        self.assertEqual(value['inputs'][0]['sha256'], lint.digest(b'original'))
        self.assert_unsupported(value, 'Input changed during analysis: main.typ')

    def test_nonentrypoint_reread_rejects_postcheck_symlink(self):
        raw = b'owned identical fixture'
        self.put('main.typ', raw)
        outside = self.base / 'outside.typ'
        outside.write_bytes(raw)
        original_check = lint.checked_path
        checks = 0
        def raced(root, name):
            nonlocal checks
            result = original_check(root, name)
            checks += 1
            if checks == 2:
                self.target.unlink()
                self.target.symlink_to(outside)
            return result
        with patch.object(lint, 'checked_path', side_effect=raced), \
                self.unavailable_parser() as parse, self.guarded_source_read():
            value = lint.report(self.root, 'check')
        parse.assert_not_called()
        self.assert_unsupported(value, 'Cannot safely reread selected input main.typ')
        self.assertEqual(outside.read_bytes(), raw)

    def test_nonentrypoint_reread_rejects_ancestor_symlink(self):
        self.put('nested/main.typ', b'owned identical fixture')
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'main.typ').write_bytes(b'owned identical fixture')
        original_check = lint.checked_path
        checks = 0
        def raced(root, name):
            nonlocal checks
            result = original_check(root, name)
            checks += 1
            if checks == 2:
                (self.root / 'nested').rename(self.root / 'retained')
                (self.root / 'nested').symlink_to(outside, target_is_directory=True)
            return result
        with patch.object(lint, 'checked_path', side_effect=raced), self.unavailable_parser() as parse:
            value = lint.report(self.root, 'check')
        parse.assert_not_called()
        self.assert_unsupported(value, 'Cannot safely reread selected input nested/main.typ')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Requires POSIX FIFO')
    def test_nonentrypoint_reread_rejects_fifo_without_opening_it(self):
        self.put('main.typ')
        original_check, original_open = lint.checked_path, os.open
        checks = 0
        swapped = False
        def raced(root, name):
            nonlocal checks, swapped
            result = original_check(root, name)
            checks += 1
            if checks == 2:
                self.target.unlink()
                os.mkfifo(self.target)
                swapped = True
            return result
        def opening(name, flags, *args, **kwargs):
            if swapped and name in (self.target, 'main.typ'):
                self.fail('A visible FIFO must not be opened during reread')
            return original_open(name, flags, *args, **kwargs)
        with patch.object(lint, 'checked_path', side_effect=raced), \
                patch.object(os, 'open', side_effect=opening), self.unavailable_parser() as parse:
            value = lint.report(self.root, 'check')
        parse.assert_not_called()
        self.assertTrue(swapped)
        self.assert_unsupported(value, 'Cannot safely reread selected input main.typ')

    def test_all_cli_modes_refuse_oversized_typst_and_markdown_without_writes(self):
        output = self.put('existing.pdf', b'OUTPUT SENTINEL')
        config = self.put('.typst-lint.toml', b'include=["*.typ","*.md"]\n')
        for suffix in ('.typ', '.md'):
            path = self.put('large' + suffix, b'x' * (syntax_client.MAX_INPUT + 1))
            for mode in ('check', 'dry-run', 'fix', 'compile'):
                with self.subTest(suffix=suffix, mode=mode):
                    command = [sys.executable, str(TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')),
                               'fix' if mode == 'dry-run' else mode, '--root', str(self.root), '--format', 'json']
                    if mode == 'dry-run':
                        command.append('--dry-run')
                    process = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                    self.assertEqual(process.returncode, 2, process.stderr)
                    self.assertEqual(process.stderr, '')
                    value = json.loads(process.stdout)
                    self.assertEqual(value['phase'], 'input')
                    self.assertIn('exceeds byte limit', value['error'])
                    self.assertFalse(value['compiler_invoked'])
                    self.assertEqual(path.stat().st_size, syntax_client.MAX_INPUT + 1)
                    self.assertEqual(output.read_bytes(), b'OUTPUT SENTINEL')
                    self.assertEqual(config.read_bytes(), b'include=["*.typ","*.md"]\n')
            path.unlink()

    def test_cli_aggregate_failure_is_structured_status_two(self):
        self.put('a.typ', b'aa')
        self.put('b.typ', b'bb')
        output = io.StringIO()
        with patch.object(lint, 'MAX_CAPTURE_BYTES', 3), redirect_stdout(output):
            status = lint.main(['check', '--root', str(self.root), '--format', 'json'])
        value = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(value['phase'], 'input')
        self.assertIn('aggregate capture byte limit (3)', value['error'])
        self.assertFalse(value['compiler_invoked'])
        self.assertEqual((self.root / 'a.typ').read_bytes(), b'aa')
        self.assertEqual((self.root / 'b.typ').read_bytes(), b'bb')


if __name__ == '__main__':
    unittest.main()
