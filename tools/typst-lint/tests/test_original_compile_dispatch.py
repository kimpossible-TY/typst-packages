"""Original-only compilation needs explicit, signal-aware gate dispatch."""
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_original_compile_cli
import offline_compile_cli
import typst_lint


class OriginalCompileDispatchTests(unittest.TestCase):
    def test_only_explicit_gate_option_selects_original_route(self):
        requested = typst_lint.gate_original_compile_requested
        self.assertTrue(requested(['compile', '--root', '/tmp', '--compile-original']))
        for args in (['check', '--compile-original'],
                     ['compile', '--root', '/tmp', '--', '--compile-original'],
                     ['compile', '--root', '--compile-original'],
                     ['compile', '--entrypoint', '--compile-original'],
                     ['compile', '--rule-set', '--compile-original']):
            with self.subTest(args=args):
                self.assertFalse(requested(args))

    def test_explicit_route_and_cooperative_signal_coordinator(self):
        def invoke(args):
            self.assertIsNotNone(offline_compile_cli._active_signals)
            self.assertEqual(args, ['--root', '/tmp', '--pdf-stdout'])
            return 0
        with mock.patch.object(document_original_compile_cli, 'main', side_effect=invoke), \
             self.assertRaises(SystemExit) as exited:
            offline_compile_cli.executable_main(
                ['compile', '--root', '/tmp', '--compile-original', '--pdf-stdout'], gate=True)
        self.assertEqual(exited.exception.code, 0)
        self.assertIsNone(offline_compile_cli._active_signals)

    def test_tail_flag_does_not_dispatch_original(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(document_original_compile_cli, 'main', side_effect=AssertionError('unexpected')), \
             mock.patch.object(typst_lint, 'report', return_value={'status': 2}), \
             redirect_stdout(io.StringIO()):
            status = typst_lint.main(['compile', '--root', root, '--format', 'json', '--',
                                      '--compile-original'], gate=True)
        self.assertEqual(status, 2)

    def test_conflicting_preview_and_original_rejected(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exited:
            typst_lint.main(['compile', '--compile-original', '--preview'], gate=True)
        self.assertEqual(exited.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
