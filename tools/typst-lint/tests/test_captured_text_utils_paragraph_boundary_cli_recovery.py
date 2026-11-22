"""Changed-length, multipass paragraph public-process and recovery acceptance.

All programs, consumers, journals and outputs are owned temporary fixtures.
Only the pinned syntax adapter and controlled fake compiler children run here.
Native Typst/fonts/PDF tools are not used; SIGKILL is sent only to owned children.
"""
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paragraph_boundary_cli_test_support import (
    PROTECTED, source_pair, assert_source_journal, assert_attributed_repair)
from paragraph_fix_test_support import same_byte_replace
from syntax_client import EXECUTABLE
from test_offline_compile_integration import OLD_OUTPUT, sha256
import test_captured_text_utils_paragraph_cli_recovery as paragraph_process

WORKER = Path(__file__).with_name('captured_text_utils_paragraph_boundary_cli_process_worker.py')
_ParagraphCliFixtures = paragraph_process._ParagraphCliFixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned-child protocol')
class ParagraphBoundaryCliRecoveryTests(_ParagraphCliFixtures):
    def fixture(self, *, source=None, present=True):
        original, expected = source_pair()
        f = super().fixture(source=original if source is None else source, present=present)
        f.expected = expected
        return f

    def run_process(self, *args, **kwargs):
        with mock.patch.object(paragraph_process, 'WORKER', WORKER):
            return super().run_process(*args, **kwargs)

    def spawn(self, *args, **kwargs):
        with mock.patch.object(paragraph_process, 'WORKER', WORKER):
            return super().spawn(*args, **kwargs)

    def assert_summary(self, f, report, original, expected, *, kind='mixed', changed=True):
        summary = report['source_summary'] if report['command'] == 'compile' else report
        self.assertEqual((summary['original_sha256'], summary['final_sha256']),
                         (sha256(original), sha256(expected)))
        self.assertEqual(summary['modified_paths'], [f.target] if changed else [])
        self.assertEqual(f.source.read_bytes(), expected)
        self.assertEqual(expected.count(PROTECTED), original.count(PROTECTED))
        assert_attributed_repair(self, summary, f.target, original, expected, kind=kind, changed=changed)
        self.assertEqual(len(summary['required_rule_ids']), 33)
        self.assertEqual(len(summary['evaluated_rule_ids']), 5)
        self.assertEqual(len(summary['not_applicable']), 28)
        self.assertFalse(summary['coverage']['global_complete'])
        self.assertTrue(summary['coverage']['input_profile_complete'])
        if changed:
            manifest, = self.journals(f, source=True)
            assert_source_journal(self, manifest, f.target, original, expected)
            self.assertEqual(manifest.parent.name, summary['journal_id'])
            self.assertEqual(self.events(f, source=True), [['staged', 'replace_intent', 'applied', 'committed']])
        else:
            self.assertIsNone(summary['journal_id'])
        return summary

    def test_public_fix_mixed_and_zero_markers_lf_crlf_bom_spacing_then_identity_noop(self):
        for kind, newline, bom in (('mixed', b'\n', b''), ('mixed', b'\r\n', b'\xef\xbb\xbf'),
                                   ('unmarked', b'\n', b'\xef\xbb\xbf'), ('unmarked', b'\r\n', b'')):
            with self.subTest(kind=kind, newline=newline, bom=bom):
                original, expected = source_pair(kind, newline, bom)
                f = self.fixture(source=original)
                before_pdf = self.complete_state(f.pdf)
                report = self.run_json(f, argv=self.argv('fix', f), actual=True)
                self.assertEqual(report['status'], 0, report)
                self.assert_no_compile(report, f)
                self.assert_summary(f, report, original, expected, kind=kind)
                self.assertGreater(len(expected), len(original))
                before = self.complete_state(f.source)
                again = self.run_json(f, argv=self.argv('fix', f), actual=True)
                self.assertEqual(again['status'], 0, again)
                self.assert_summary(f, again, expected, expected, changed=False)
                self.assertEqual(self.complete_state(f.source), before)
                self.assertEqual(self.complete_state(f.pdf), before_pdf)
                self.assertEqual(self.journals(f), [])
                self.preserved(f)

    def test_default_ten_second_fake_compile_receipts_contain_only_final_changed_length_source(self):
        original, expected = source_pair()
        started = time.monotonic()
        report = self.run_json(assert_order=True)
        elapsed = time.monotonic() - started
        self.assertEqual((report['status'], report['source_commit'], report['compiler_status']),
                         (0, 'committed', 0), report)
        self.assert_summary(self.f, report, original, expected)
        self.assertIn(expected.hex().encode(), self.f.pdf.read_bytes())
        self.assertNotIn(original.hex().encode(), self.f.pdf.read_bytes())
        attempts = report['compile_report']['attempted_invocations']
        self.assertEqual([(a['purpose'], a['returncode']) for a in attempts],
                         [('version-probe', 0), ('compile', 0)])
        self.assertEqual(attempts, report['publication_report']['attempted_invocations'])
        observation = report['package_observation']
        self.assertEqual(observation['root_source']['sha256'], sha256(expected))
        self.assertEqual(observation['root_source']['size'], len(expected))
        argv = attempts[-1]['argv']
        packages = Path(argv[argv.index('--package-path') + 1])
        leaf = packages / 'local/text-utils/0.1.2'
        observed = observation['dependency_paths']
        self.assertEqual(len(observed), 3)
        self.assertEqual(set(observed), {self.f.target, str(leaf / 'lib.typ'), str(leaf / 'typst.toml')})
        raw = b'\0'.join(os.fsencode(path) for path in observed) + b'\0'
        self.assertEqual((observation['dependencies']['sha256'], observation['dependencies']['size']),
                         (sha256(raw), len(raw)))
        self.assertFalse(Path(attempts[-1]['cwd']).parent.exists())
        self.assertTrue(all(not Path(a['argv'][0]).exists() for a in attempts))
        publication = report['publication_report']
        self.assertEqual(publication['observed_sha256'], sha256(self.f.pdf.read_bytes()))
        manifest, = self.journals()
        journal = json.loads(manifest.read_bytes())
        self.assertEqual((journal['schema'], journal['destination'], journal['old']['sha256']),
                         (1, self.f.output, sha256(OLD_OUTPUT)))
        events = [json.loads(path.read_bytes()) for path in sorted(manifest.parent.glob('event-*.json'))]
        self.assertEqual(events[2]['state'], 'compiled')
        self.assertEqual((events[2]['pdf_sha256'], events[-1]['postimage']['sha256']),
                         (sha256(self.f.pdf.read_bytes()), sha256(self.f.pdf.read_bytes())))
        self.assertEqual(publication['source_journal_id'], report['source_summary']['journal_id'])
        self.assertEqual(self.events()[0][-1], 'published')
        self.preserved()
        self.assert_lock_free()
        print('PARAGRAPH_BOUNDARY_FAKE_DEFAULT10_SECONDS ' + str(round(elapsed, 3)), flush=True)

    def test_compiler_failure_preserves_committed_multipass_source_and_previous_pdf(self):
        original, expected = source_pair()
        before = self.complete_state(self.f.pdf)
        report = self.run_json(compiler_mode='nonzero', assert_order=True)
        self.assertEqual((report['status'], report['source_commit'], report['compiler_status']),
                         (19, 'committed', 19), report)
        self.assert_summary(self.f, report, original, expected)
        self.assertEqual(self.complete_state(self.f.pdf), before)
        publication = report['publication_report']
        self.assertEqual((publication['phase'], publication['output_disposition'], publication['recovery_required']),
                         ('aborted_unpublished', 'untouched', False))
        self.assertEqual(self.events()[0], ['prepared', 'compile_launch_intent', 'aborted_unpublished'])
        self.preserved()

    def test_compiler_dependency_refusal_preserves_real_source_commit_and_previous_pdf(self):
        original, expected = source_pair()
        before = self.complete_state(self.f.pdf)
        report = self.run_json(compiler_mode='refuse-dependencies', assert_order=True)
        self.assertEqual((report['status'], report['source_commit'], report['compiler_status']),
                         (3, 'committed', 0), report)
        self.assert_summary(self.f, report, original, expected)
        self.assertEqual(self.complete_state(self.f.pdf), before)
        publication = report['publication_report']
        self.assertEqual((publication['phase'], publication['output_disposition'], publication['recovery_required']),
                         ('aborted_unpublished', 'untouched', False))
        self.assertEqual(self.events()[0], ['prepared', 'compile_launch_intent', 'aborted_unpublished'])
        self.preserved()
        self.assert_lock_free()

    def test_source_sigkill_before_after_changed_length_replace_historical_resource_free_recovery(self):
        original, expected = source_pair()
        for point in ('before_replace', 'after_replace'):
            with self.subTest(point=point):
                f = self.fixture()
                output_id = self.crash(f, domain='source', point=point)
                source_manifest, = self.journals(f, source=True)
                assert_source_journal(self, source_manifest, f.target, original, expected)
                self.assertEqual(f.source.read_bytes(), original if point == 'before_replace' else expected)
                for path in (f.config, f.catalog, f.manifest, f.package, f.base / 'owned paragraph compiler'):
                    path.unlink()
                f.package.parent.rmdir()
                output = self.run_json(f, argv=self.recover_argv(output_id, f), fake_compiler=False, forbid_source=True)
                self.assertEqual(output['status'], 0, output)
                source = self.run_json(f, argv=self.source_recover_argv(source_manifest.parent.name, f), fake_compiler=False)
                self.assertEqual(source['status'], 0, source)
                self.assertTrue(source['recovery_report']['complete'])
                self.assertEqual(f.source.read_bytes(), original)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assertEqual(self.events(f, source=True)[0][-1], 'recovered')
                self.assert_lock_free(f)

    def test_output_sigkill_changed_length_commit_uncertain_rename_and_durable_publication(self):
        _, expected = source_pair()
        for point, path in (('after_staged', None), ('after_replace_before_event', None),
                            ('before_published_event', None), ('after_event', 'published')):
            with self.subTest(point=point):
                f = self.fixture()
                output_id = self.crash(f, point=point, path=path)
                final = self.complete_state(f.pdf)
                self.assertEqual(f.source.read_bytes(), expected)
                report = self.run_json(f, argv=self.recover_argv(output_id, f), fake_compiler=False, forbid_source=True)
                uncertain = point == 'after_replace_before_event'
                self.assertEqual(report['status'], 3 if uncertain else 0, report)
                publication = report['publication_report']
                self.assertEqual(publication['invocation_origin'], 'historical_recovery')
                self.assertEqual(publication['attempted_invocations'], [])
                self.assertFalse(publication['invocation_details_available'])
                if uncertain:
                    self.assertEqual((publication['output_disposition'], publication['recovery_required']),
                                     ('preservedconflict', True))
                    self.assertEqual(self.complete_state(f.pdf), final)
                    inventory = self.run_json(f, argv=self.argv('output-status', f), fake_compiler=False)
                    self.assertIn(output_id, inventory['output_inventory']['pending'])
                elif path == 'published':
                    self.assertEqual(self.complete_state(f.pdf), final)
                    self.assertEqual(self.events(f)[0][-1], 'published')
                else:
                    self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assertEqual(f.source.read_bytes(), expected)
                self.preserved(f)

    def test_later_edit_equal_bytes_new_inode_are_preserved_after_changed_length_commit(self):
        _, expected = source_pair()
        for domain in ('source', 'output'):
            for change in ('edit', 'inode'):
                with self.subTest(domain=domain, change=change):
                    f = self.fixture()
                    output_id = self.crash(f, domain=domain,
                        point='after_replace' if domain == 'source' else 'after_replaced')
                    self.assertEqual(f.source.read_bytes(), expected)
                    path = f.source if domain == 'source' else f.pdf
                    if change == 'edit':
                        path.write_bytes(path.read_bytes() + b'\nLater private user edit.\n')
                    else:
                        same_byte_replace(path)
                    before = self.complete_state(path)
                    args = (self.source_recover_argv(self.journals(f, source=True)[0].parent.name, f)
                            if domain == 'source' else self.recover_argv(output_id, f))
                    report = self.run_json(f, argv=args, fake_compiler=False)
                    self.assertEqual(report['status'], 3, report)
                    self.assertEqual(self.complete_state(path), before)
                    self.preserved(f)
                    self.assert_lock_free(f)


if __name__ == '__main__':
    unittest.main()
