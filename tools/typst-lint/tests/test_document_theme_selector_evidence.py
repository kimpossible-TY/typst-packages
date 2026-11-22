"""A callback selector proof requires captured lexical and package provenance."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding
import document_footnote_evidence
import document_resources
import document_sequence_preview
import document_theme_selector_evidence as theme
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ThemeSelectorEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.project, self.packages, self.cache = (root / name for name in ('project', 'packages', 'cache'))
        for directory in (self.project, self.packages, self.cache):
            directory.mkdir()
        fixtures = TOOL / 'tests/fixtures/reviewed-packages/local'
        for name, version in (('text-utils', '0.1.2'), ('math-blocks', '0.2.0'), ('project-references', '0.1.0')):
            shutil.copytree(fixtures / name / version, self.packages / 'local' / name / version)
        (self.project / 'styles.typ').write_bytes(b'#import "@local/math-blocks:0.2.0": *\n')
        self.prefix = b'#import "styles.typ": *\n'
        self.body = (b'#use(x => [#context [\n#let theme = theme-from-text-fill()\n'
                     b'$ A #dots_space #footnote[Note] $\n]])')

    def proof(self, source, *, omit=None, change_project=False):
        (self.project / 'main.typ').write_bytes(source)
        ctx = include_context.load_include_context(self.project, (('main.typ', source),))
        namespaces = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = conditional_footnote_binding.ConditionalFootnoteBinding(ctx, namespaces)
        project_ref = self.packages / 'local/project-references/0.1.0/lib.typ'
        if change_project:
            project_ref.write_bytes(project_ref.read_bytes() + b'\n// changed\n')
        files = [('project', item.path, 'source') for item in session._graph.sources]
        for name, version in (('text-utils', '0.1.2'), ('math-blocks', '0.2.0'), ('project-references', '0.1.0')):
            files.extend(('packages', f'local/{name}/{version}/{part}', 'package')
                         for part in ('typst.toml', 'lib.typ'))
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*row)
                                                       for row in files if row[1] != omit])
        return document_footnote_evidence.PreviewFootnoteEvidence(session, snapshot)

    def supports(self, proof, source):
        tree = parse(source)
        closure = next(node for node in tree.nodes if node.kind == 'Closure')
        lets = tuple((node.start, node.end) for node in tree.nodes
                     if node.kind == 'LetBinding' and closure.start <= node.start and node.end <= closure.end)
        return theme.supports_theme_lets(proof, 'main.typ', source, closure.start, closure.end, lets)

    def test_facade_selector_is_conditional_and_staged_source_is_unchanged(self):
        source = self.prefix + self.body
        proof = self.proof(source)
        self.assertTrue(self.supports(proof, source))
        result = document_sequence_preview.preview(parse(source), 'main.typ', load_rules()[0]['rules'],
            profile='conditional-gaps-and-spacers', footnote_evidence=proof, classify_deferred=True)
        self.assertEqual(result.get('reason_code'), 'unsupported-deferred-footnote-binding', result)
        staged = document_resources.stage_document(proof._session._context, [], load_rules()[0]['rules'],
            proof._snapshot, profile='conditional-gaps-and-spacers', footnote_evidence=proof,
            partial_unsupported=True, withheld_records=[result])
        self.assertEqual(staged['project', 'main.typ'], source)
        self.assertFalse(result['steps'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_shadow_unknown_wildcard_and_foreign_helper_are_not_proved(self):
        for middle in (b'#let theme-from-text-fill = none\n', b'#import "unknown.typ": *\n',
                       b'#import "@local/other:0.1.0": theme-from-text-fill\n'):
            with self.subTest(middle=middle):
                source = self.prefix + middle + self.body
                proof = self.proof(source)
                self.assertFalse(self.supports(proof, source))

    def test_wrong_let_parameters_and_assignment_are_rejected(self):
        for body in (self.body.replace(b'let theme =', b'let footnote ='),
                     self.body.replace(b'theme-from-text-fill()', b'theme-from-text-fill(x)'),
                     self.body.replace(b'let theme = theme-from-text-fill()', b'let theme = unknown()'),
                     self.body.replace(b'use(x =>', b'use(theme-from-text-fill =>'),
                     self.body.replace(b'#let theme', b'#{footnote = x}\n#let theme')):
            with self.subTest(body=body):
                source = self.prefix + body
                proof = self.proof(source)
                with self.assertRaises(SafetyError):
                    self.supports(proof, source)

    def test_missing_or_changed_transitive_project_reference_refuses(self):
        source = self.prefix + self.body
        for omit in ('local/project-references/0.1.0/typst.toml',
                     'local/project-references/0.1.0/lib.typ'):
            with self.subTest(omit=omit):
                proof = self.proof(source, omit=omit)
                with self.assertRaises(SafetyError):
                    self.supports(proof, source)
        proof = self.proof(source, change_project=True)
        with self.assertRaises(SafetyError):
            self.supports(proof, source)

    def test_project_reference_cache_selection_rejects_upper_root_shadow(self):
        original = self.packages / 'local/project-references/0.1.0'
        cached = self.cache / 'local/project-references/0.1.0'
        cached.parent.mkdir(parents=True)
        shutil.move(str(original), str(cached))
        source = self.prefix + self.body
        (self.project / 'main.typ').write_bytes(source)
        ctx = include_context.load_include_context(self.project, (('main.typ', source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = conditional_footnote_binding.ConditionalFootnoteBinding(ctx, ns)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', item.path, 'source') for item in session._graph.sources]
        for name, version in (('text-utils', '0.1.2'), ('math-blocks', '0.2.0')):
            files.extend(('packages', f'local/{name}/{version}/{part}', 'package')
                         for part in ('typst.toml', 'lib.typ'))
        files.extend(('cache', f'local/project-references/0.1.0/{part}', 'package')
                     for part in ('typst.toml', 'lib.typ'))
        def capture(extra=()):
            snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*row)
                                                           for row in (*files, *extra)])
            return document_footnote_evidence.PreviewFootnoteEvidence(session, snapshot)
        self.assertTrue(self.supports(capture(), source))
        shadow = self.packages / 'Local/Project-References/0.1.0'
        shadow.mkdir(parents=True)
        (shadow / 'typst.toml').write_text('unreviewed')
        with self.assertRaises(SafetyError):
            self.supports(capture((('packages', 'Local/Project-References/0.1.0/typst.toml', 'package'),)), source)

    def test_literal_palette_field_requires_prior_proven_selector_and_common_key(self):
        body = self.body.replace(b'$ A', b'#let color = theme.callouts.theorem.border\n$ A')
        source = self.prefix + body
        proof = self.proof(source)
        self.assertTrue(self.supports(proof, source))
        result = document_sequence_preview.preview(parse(source), 'main.typ', load_rules()[0]['rules'],
            profile='conditional-gaps-and-spacers', footnote_evidence=proof, classify_deferred=True)
        self.assertEqual(result.get('reason_code'), 'unsupported-deferred-footnote-binding', result)
        staged = document_resources.stage_document(proof._session._context, [], load_rules()[0]['rules'],
            proof._snapshot, profile='conditional-gaps-and-spacers', footnote_evidence=proof,
            partial_unsupported=True, withheld_records=[result])
        self.assertEqual(staged['project', 'main.typ'], source)
        for replacement in (b'theme.callouts.missing.border', b'theme.callouts.theorem.border()',
                            b'other.callouts.theorem.border', b'theme.callouts.at("theorem")', b'theme'):
            with self.subTest(replacement=replacement):
                candidate = source.replace(b'theme.callouts.theorem.border', replacement)
                with self.assertRaises(SafetyError):
                    self.supports(proof, candidate)
        before = body.replace(b'#let theme = theme-from-text-fill()\n#let color = theme.callouts.theorem.border',
                              b'#let color = theme.callouts.theorem.border\n#let theme = theme-from-text-fill()')
        with self.assertRaises(SafetyError):
            self.supports(proof, self.prefix + before)

    def test_palette_field_cannot_shadow_protected_names_or_escape(self):
        source = self.prefix + self.body.replace(b'$ A', b'#let color = theme.callouts.theorem.border\n$ A')
        proof = self.proof(source)
        for name in (b'theme', b'footnote', b'cetz', b'theme-from-text-fill'):
            with self.subTest(name=name), self.assertRaises(SafetyError):
                self.supports(proof, source.replace(b'#let color =', b'#let ' + name + b' ='))
        for extra in (b'#unknown(theme)\n', b'#unknown(theme.callouts)\n',
                      b'#let leaked = theme.callouts\n', b'#{theme.remove("callouts")}\n',
                      b'#{theme.insert("callouts", none)}\n',
                      b'#{theme.callouts.insert("theorem", none)}\n',
                      b'#{theme.callouts = none}\n', b'$ theme $\n',
                      b'$ theme.remove("callouts") $\n'):
            with self.subTest(extra=extra), self.assertRaises(SafetyError):
                self.supports(proof, source.replace(b'#let color =', extra + b'#let color ='))


if __name__ == '__main__':
    unittest.main()
