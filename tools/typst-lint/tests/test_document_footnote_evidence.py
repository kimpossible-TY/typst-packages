"""Private preview snapshot binding for conditional footnote calls."""
from dataclasses import replace
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding as binding
import document_footnote_evidence as preview
import document_resources
import document_sequence_preview
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class PreviewFootnoteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (base / name for name in ('project', 'packages', 'cache'))
        for directory in (self.project, self.packages, self.cache):
            directory.mkdir()
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/text-utils/0.1.2',
                        self.packages / 'local/text-utils/0.1.2')
        self.source = b'#import "styles.typ": *\n#block[$ x #dots_space #footnote[Note] $]'
        self.facade = b'#import "@local/text-utils:0.1.2": *\n'
        (self.project / 'main.typ').write_bytes(self.source)
        (self.project / 'styles.typ').write_bytes(self.facade)

    def capture(self, *, missing=()):
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                 ('packages', 'local/text-utils/0.1.2/typst.toml', 'package'),
                 ('packages', 'local/text-utils/0.1.2/lib.typ', 'package')]
        requests = [document_resources.ResourceRequest(*row) for row in files if row[1] not in missing]
        snapshot = document_resources.capture(roots, requests)
        return preview.PreviewFootnoteEvidence(session, snapshot), session, snapshot

    def test_candidate_is_reparsed_and_call_offsets_are_source_bound(self):
        proof, _, _ = self.capture()
        candidate = self.source.replace(b'#dots_space ', b'')
        calls = proof.candidate_calls('main.typ', candidate)
        self.assertEqual(len(calls.calls), 1)
        call = calls.calls[0]
        self.assertEqual(candidate[call.start:call.end], b'#footnote[Note]')
        self.assertEqual(call.source_sha256, preview.digest(candidate))
        self.assertFalse(calls.runtime_binding_proved or calls.edit_permission or calls.compiler_permission)
        proof.validate_staged_facades({'main.typ': candidate}, {'main.typ': (call.start,)})

    def test_combined_import_alias_cannot_restore_builtin_footnote(self):
        prefix = b'#import "styles.typ": *\n'
        call = b'#block[$ x #dots_space #footnote[Note] $]'
        cases = (
            (b'#import "styles.typ" as footnote: paragraph_tab\n', 0),
            (b'#import "styles.typ" as footnote: paragraph_tab as footnote\n', 0),
            (b'#import "styles.typ" as other: paragraph_tab as footnote\n', 0),
            (b'#import "styles.typ" as other: paragraph_tab\n', 1),
        )
        for statement, expected in cases:
            with self.subTest(statement=statement):
                self.source = prefix + statement + call
                (self.project / 'main.typ').write_bytes(self.source)
                proof, session, snapshot = self.capture()
                self.assertEqual(len(proof.candidate_calls('main.typ', self.source).calls), expected)
                record = document_sequence_preview.preview(session._context.source_for('main.typ').tree,
                    'main.typ', load_rules()[0]['rules'], profile='conditional-gaps-and-spacers',
                    footnote_evidence=proof)
                if expected:
                    self.assertNotEqual(record['verification'], 'withheld', record)
                    document_resources.stage_document(session._context, [record], load_rules()[0]['rules'],
                        snapshot, profile='conditional-gaps-and-spacers', footnote_evidence=proof)
                else:
                    self.assertTrue(record is None or record['verification'] == 'withheld', record)

    def test_combined_wildcard_proof_and_shadow_remain_distinct(self):
        call = b'#block[$ x #dots_space #footnote[Note] $]'
        for alias, expected in ((b'other', 1), (b'footnote', 0)):
            with self.subTest(alias=alias):
                self.source = b'#import "styles.typ" as ' + alias + b': *\n' + call
                (self.project / 'main.typ').write_bytes(self.source)
                proof, session, snapshot = self.capture()
                self.assertEqual(len(proof.candidate_calls('main.typ', self.source).calls), expected)
                record = document_sequence_preview.preview(session._context.source_for('main.typ').tree,
                    'main.typ', load_rules()[0]['rules'], profile='conditional-gaps-and-spacers',
                    footnote_evidence=proof)
                if expected:
                    self.assertNotEqual(record['verification'], 'withheld', record)
                    document_resources.stage_document(session._context, [record], load_rules()[0]['rules'],
                        snapshot, profile='conditional-gaps-and-spacers', footnote_evidence=proof)
                else:
                    self.assertTrue(record is None or record['verification'] == 'withheld', record)

    def test_shadow_and_dynamic_import_are_recomputed_per_candidate(self):
        proof, _, _ = self.capture()
        for prefix in (b'#let footnote = none\n', b'#import "unknown.typ": *\n'):
            with self.subTest(prefix=prefix):
                self.assertFalse(proof.candidate_calls('main.typ', prefix + self.source).calls)

    def test_missing_or_changed_graph_and_package_bytes_refuse(self):
        with self.assertRaises(SafetyError):
            self.capture(missing=('styles.typ',))
        for missing in ('typst.toml', 'lib.typ'):
            with self.subTest(missing=missing):
                path = 'local/text-utils/0.1.2/' + missing
                proof, _, _ = self.capture(missing=(path,))
                with self.assertRaises(SafetyError):
                    proof.candidate_calls('main.typ', self.source)
        proof, _, snapshot = self.capture()
        with self.assertRaises(SafetyError):
            preview.PreviewFootnoteEvidence(proof._session, replace(snapshot))

    def test_changed_final_facade_and_forged_offsets_refuse(self):
        proof, _, _ = self.capture()
        result = proof.candidate_calls('main.typ', self.source)
        offset = result.calls[0].start
        with self.assertRaises(SafetyError):
            proof.validate_staged_facades({'main.typ': self.source, 'styles.typ': self.facade + b'\n'},
                                           {'main.typ': (offset,)})
        with self.assertRaises(SafetyError):
            proof.validate_staged_facades({'main.typ': self.source}, {'main.typ': (offset + 1,)})

    def test_mutated_session_or_snapshot_refuses(self):
        proof, session, _ = self.capture()
        object.__setattr__(session._graph.sources[0], 'source', b'changed')
        with self.assertRaises(SafetyError):
            proof.candidate_calls('main.typ', self.source)

    def test_cache_selection_refuses_new_higher_priority_staged_package(self):
        package = self.packages / 'local/text-utils/0.1.2'
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.move(str(package), str(cached))
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        # A new first-root file exists in the snapshot taken after the issued
        # cache selection; its presence changes the private compiler's choice.
        package.mkdir(parents=True)
        (package / 'typst.toml').write_text('unreviewed')
        files = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                 ('packages', 'local/text-utils/0.1.2/typst.toml', 'package'),
                 ('cache', 'local/text-utils/0.1.2/typst.toml', 'package'),
                 ('cache', 'local/text-utils/0.1.2/lib.typ', 'package')]
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x) for x in files])
        proof = preview.PreviewFootnoteEvidence(session, snapshot)
        with self.assertRaises(SafetyError):
            proof.candidate_calls('main.typ', self.source)

    def test_cache_selection_rejects_case_variant_higher_priority_path(self):
        original = self.packages / 'local/text-utils/0.1.2'
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.move(str(original), str(cached))
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        shadow = self.packages / 'Local/Text-Utils/0.1.2'
        shadow.mkdir(parents=True)
        (shadow / 'typst.toml').write_text('unreviewed')
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                 ('packages', 'Local/Text-Utils/0.1.2/typst.toml', 'package'),
                 ('cache', 'local/text-utils/0.1.2/typst.toml', 'package'),
                 ('cache', 'local/text-utils/0.1.2/lib.typ', 'package')]
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x) for x in files])
        proof = preview.PreviewFootnoteEvidence(session, snapshot)
        with self.assertRaises(SafetyError):
            proof.candidate_calls('main.typ', self.source)

    def test_cache_selection_refuses_version_and_ancestor_files_only(self):
        original = self.packages / 'local/text-utils/0.1.2'
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.move(str(original), str(cached))
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        common = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                  ('cache', 'local/text-utils/0.1.2/typst.toml', 'package'),
                  ('cache', 'local/text-utils/0.1.2/lib.typ', 'package')]
        for path, blocked in (('local', True), ('local/text-utils', True),
                              ('local/text-utils/0.1.2', True),
                              ('local/text-utils/0.1.3/typst.toml', False),
                              ('local/other/0.1.2/typst.toml', False)):
            with self.subTest(path=path):
                top = self.packages / 'local'
                if top.is_dir():
                    shutil.rmtree(top)
                elif top.exists():
                    top.unlink()
                target = self.packages / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('unreviewed')
                files = (*common, ('packages', path, 'package'))
                snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x) for x in files])
                proof = preview.PreviewFootnoteEvidence(session, snapshot)
                if blocked:
                    with self.assertRaises(SafetyError):
                        proof.candidate_calls('main.typ', self.source)
                else:
                    self.assertEqual(len(proof.candidate_calls('main.typ', self.source).calls), 1)

    def test_package_changed_between_namespace_and_snapshot_is_rejected(self):
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        package = self.packages / 'local/text-utils/0.1.2/lib.typ'
        package.write_bytes(package.read_bytes() + b'\n// modified\n')
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                 ('packages', 'local/text-utils/0.1.2/typst.toml', 'package'),
                 ('packages', 'local/text-utils/0.1.2/lib.typ', 'package')]
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x) for x in files])
        proof = preview.PreviewFootnoteEvidence(session, snapshot)
        with self.assertRaises(SafetyError):
            proof.candidate_calls('main.typ', self.source)

    def test_changed_transitive_wildcard_namespace_is_rejected_before_staging(self):
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/scoped-annotations/0.3.0',
                        self.packages / 'local/scoped-annotations/0.3.0')
        shutil.copytree(TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0',
                        self.cache / 'preview/mannot/0.4.0')
        facade = b'#import "@local/scoped-annotations:0.3.0": *\n'
        (self.project / 'styles.typ').write_bytes(facade)
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        mannot = self.cache / 'preview/mannot/0.4.0/src/lib.typ'
        mannot.write_bytes(mannot.read_bytes() + b'\n#let footnote(body) = [changed]\n')
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                 ('packages', 'local/scoped-annotations/0.3.0/typst.toml', 'package'),
                 ('packages', 'local/scoped-annotations/0.3.0/lib.typ', 'package'),
                 ('cache', 'preview/mannot/0.4.0/typst.toml', 'package'),
                 ('cache', 'preview/mannot/0.4.0/src/lib.typ', 'package')]
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x) for x in files])
        proof = preview.PreviewFootnoteEvidence(session, snapshot)
        with self.assertRaises(SafetyError):
            proof.candidate_calls('main.typ', self.source)
        direct = self.source.replace(b'#import "styles.typ": *',
                                     b'#import "@local/scoped-annotations:0.3.0": *')
        with self.assertRaises(SafetyError):
            proof.candidate_calls('main.typ', direct)

    def test_transitive_wildcard_namespace_requires_selected_manifest_and_source(self):
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/scoped-annotations/0.3.0',
                        self.packages / 'local/scoped-annotations/0.3.0')
        shutil.copytree(TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0',
                        self.cache / 'preview/mannot/0.4.0')
        (self.project / 'styles.typ').write_bytes(b'#import "@local/scoped-annotations:0.3.0": *\n')
        context = include_context.load_include_context(self.project, (('main.typ', self.source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = binding.ConditionalFootnoteBinding(context, ns)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', 'main.typ', 'source'), ('project', 'styles.typ', 'source'),
                 ('packages', 'local/scoped-annotations/0.3.0/typst.toml', 'package'),
                 ('packages', 'local/scoped-annotations/0.3.0/lib.typ', 'package'),
                 ('cache', 'preview/mannot/0.4.0/typst.toml', 'package'),
                 ('cache', 'preview/mannot/0.4.0/src/lib.typ', 'package')]
        for omitted in (None, 'preview/mannot/0.4.0/typst.toml', 'preview/mannot/0.4.0/src/lib.typ'):
            with self.subTest(omitted=omitted):
                snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x)
                                                              for x in files if x[1] != omitted])
                proof = preview.PreviewFootnoteEvidence(session, snapshot)
                if omitted is None:
                    self.assertEqual(len(proof.candidate_calls('main.typ', self.source).calls), 1)
                else:
                    with self.assertRaises(SafetyError):
                        proof.candidate_calls('main.typ', self.source)
        shadow = self.packages / 'preview/mannot/0.4.0'
        shadow.mkdir(parents=True)
        (shadow / 'typst.toml').write_text('unreviewed')
        extra = ('packages', 'preview/mannot/0.4.0/typst.toml', 'package')
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x)
                                                       for x in (*files, extra)])
        with self.assertRaises(SafetyError):
            preview.PreviewFootnoteEvidence(session, snapshot).candidate_calls('main.typ', self.source)

    def test_hash_chained_preview_and_staging_revalidate_evidence(self):
        proof, session, snapshot = self.capture()
        catalog = load_rules()[0]['rules']
        context = session._context
        source = next(item for item in context.sources if item.path == 'main.typ')
        profile = 'conditional-gaps-and-spacers'
        without = document_sequence_preview.preview(source.tree, 'main.typ', catalog, profile=profile)
        self.assertEqual(without['verification'], 'withheld')
        record = document_sequence_preview.preview(source.tree, 'main.typ', catalog, profile=profile,
                                                   footnote_evidence=proof)
        self.assertNotEqual(record['verification'], 'withheld', record)
        self.assertEqual(record['steps'][-1]['rule_id'], 'pde.unaligned-footnote-no-spacer')
        self.assertEqual(len(record['steps'][-1]['conditional_footnote_calls']), 1)
        staged = document_resources.stage_document(context, [record], catalog, snapshot, profile=profile,
                                                   footnote_evidence=proof)
        self.assertNotIn(b'#dots_space', staged['project', 'main.typ'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)
        with self.assertRaises(SafetyError):
            document_resources.stage_document(context, [record], catalog, snapshot, profile=profile)
        forged = dict(record)
        forged['candidate_sha256'] = '0' * 64
        with self.assertRaises(SafetyError):
            document_resources.stage_document(context, [forged], catalog, snapshot, profile=profile,
                                              footnote_evidence=proof)

    def test_literal_callback_candidate_is_reproved_during_staging(self):
        self.source = (b'#import "styles.typ": *\n'
                       b'#use(x => [\n$ A #dots_space #footnote[Note] $\n])')
        (self.project / 'main.typ').write_bytes(self.source)
        proof, session, snapshot = self.capture()
        catalog = load_rules()[0]['rules']
        context = session._context
        tree = next(item.tree for item in context.sources if item.path == 'main.typ')
        calls = proof.candidate_calls('main.typ', self.source)
        self.assertEqual(len(calls.calls), 1)
        self.assertIn('captured at its declaration', calls.calls[0].conditions[-1])
        profile = 'conditional-gaps-and-spacers'
        record = document_sequence_preview.preview(tree, 'main.typ', catalog, profile=profile,
                                                   footnote_evidence=proof)
        self.assertNotEqual(record['verification'], 'withheld', record)
        staged = document_resources.stage_document(context, [record], catalog, snapshot, profile=profile,
                                                   footnote_evidence=proof)
        self.assertNotIn(b'#dots_space', staged['project', 'main.typ'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    def test_callback_declared_in_dynamic_ancestor_is_not_capture_evidence(self):
        prefix = b'#import "styles.typ": *\n#let footnote = footnote\n'
        bodies = (
            b'#for n in (0,1) { let f = x => [#footnote[Note]]; f(0); footnote = none }',
            b'#while false { let f = x => [#footnote[Note]]; f(0) }',
            b'#if true { let f = x => [#footnote[Note]]; f(0) }',
            b'#let outer() = { let f = x => [#footnote[Note]]; f(0) }',
        )
        for body in bodies:
            with self.subTest(body=body):
                self.source = prefix + body
                (self.project / 'main.typ').write_bytes(self.source)
                proof, _, _ = self.capture()
                self.assertFalse(proof.candidate_calls('main.typ', self.source).calls)
                offset = self.source.index(b'#footnote[Note]')
                for allow in (False, True):
                    with self.subTest(allow_deferred=allow), self.assertRaises(SafetyError):
                        proof.validate_staged_facades({'main.typ': self.source},
                                                      {'main.typ': (offset,)}, allow_deferred=allow)

    def test_multiple_root_callbacks_keep_independent_valid_captures(self):
        self.source = (b'#import "styles.typ": *\n'
                       b'#use(x => [#footnote[First]], y => [#footnote[Second]])')
        (self.project / 'main.typ').write_bytes(self.source)
        proof, _, _ = self.capture()
        result = proof.candidate_calls('main.typ', self.source)
        self.assertEqual({call.start for call in result.calls},
                         {self.source.index(b'#footnote[First]'), self.source.index(b'#footnote[Second]')})

    @unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
    def test_typst_loop_callback_binding_can_change_between_iterations(self):
        source = (b'#import "styles.typ": *\n#let original = footnote\n#let footnote = footnote\n'
                  b'#for n in (0,1) { let f = x => [#metadata(type(footnote) == type(original)) <probe>]; '
                  b'f(0); footnote = none }')
        path = self.project / 'query.typ'
        path.write_bytes(source)
        run = subprocess.run([shutil.which('typst'), 'query', '--root', str(self.project),
            '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
            '--field', 'value', '--format', 'json', str(path), '<probe>'], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [True, False])


if __name__ == '__main__':
    unittest.main()
