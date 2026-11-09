"""A separate, exact modern text-utils artifact grants diagnostics only."""
from dataclasses import replace
from pathlib import Path
import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding as binding
import conditional_modern_highlight as modern
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest

MODERN = TOOL / 'tests/fixtures/reviewed-text-utils-main/local/text-utils/0.1.2'
OLD = TOOL / 'tests/fixtures/reviewed-packages/local/text-utils/0.1.2'
IMPORT = b'#import "@local/text-utils:0.1.2": *\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalModernHighlightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='modern-highlight-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (root / name for name in ('project', 'packages', 'cache'))
        for folder in (self.project, self.packages, self.cache):
            folder.mkdir()
        self.target = self.packages / 'local/text-utils/0.1.2'
        self.target.parent.mkdir(parents=True)
        shutil.copytree(MODERN, self.target)

    def session(self, source):
        (self.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        namespaces = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        return binding.ConditionalFootnoteBinding(context, namespaces), context

    def run_source(self, source):
        session, context = self.session(source)
        return modern.ModernHighlightAnalyzer(session, scoped_callbacks=True).analyze(
            context.source_for('main.typ').tree, 'main.typ')

    def test_default_flow_phrase_and_named_body_have_separate_conditional_evidence(self):
        cases = ((b'#highlighted[$ x $]', 'flow', 'default'),
                 (b'#highlighted(mode: "flow")[$ x $]', 'flow', 'explicit'),
                 (b'#highlighted(mode: "phrase")[$ x $]', 'phrase', 'explicit'))
        for call, mode, mode_source in cases:
            with self.subTest(call=call):
                source = IMPORT + call
                report = self.run_source(source)
                finding, = report['diagnostics']
                self.assertEqual(finding['mode'], mode)
                self.assertEqual(finding['mode_source'], mode_source)
                self.assertEqual(finding['rule_id'], 'pde.text-only-highlight')
                self.assertEqual(report['source_sha256'], digest(source))
                self.assertEqual(report['package_evidence']['source_sha256'], modern.MODERN_SOURCE_SHA256)
                self.assertEqual(report['artifact'], modern.REVIEWED_VARIANT)
                self.assertTrue(finding['conditions'] and finding['route'])
                for key in ('runtime_binding_proved', 'execution_proved', 'coverage_complete',
                            'edit_permission', 'compiler_permission', 'render_equivalence_proved'):
                    self.assertFalse(report[key], (key, report))
                self.assertFalse(finding['edit_permission'])

    def test_text_only_is_not_a_finding_and_unknown_mode_is_unresolved(self):
        text = self.run_source(IMPORT + b'#highlighted(mode: "phrase")[Only prose.]')
        self.assertFalse(text['diagnostics'] or text['unresolved'])
        for option in (b'mode: key', b'mode: "other"', b'extra: true'):
            with self.subTest(option=option):
                report = self.run_source(IMPORT + b'#highlighted(' + option + b')[$ x $]')
                self.assertFalse(report['diagnostics'])
                self.assertTrue(report['unresolved'])
        with self.assertRaises(include_context.IncludeSyntaxError):
            self.session(IMPORT + b'#highlighted(mode: "flow", mode: "phrase")[$ x $]')
        named_body = self.run_source(IMPORT + b'#highlighted(body: [$ x $], mode: "phrase")')
        self.assertFalse(named_body['diagnostics'])
        self.assertTrue(named_body['unresolved'])

    def test_shadow_unknown_import_suppression_and_opaque_body(self):
        bodies = (b'#let highlighted = none\n#highlighted[$ x $]',
                  b'#import "unknown.typ": *\n#highlighted[$ x $]')
        for body in bodies:
            with self.subTest(body=body):
                (self.project / 'unknown.typ').write_bytes(b'#let highlighted = none')
                report = self.run_source(IMPORT + body)
                self.assertFalse(report['diagnostics'])
        suppressed = self.run_source(IMPORT +
            b'// typst-lint: ignore-next pde.text-only-highlight -- deliberate\n#highlighted[$ x $]')
        self.assertFalse(suppressed['diagnostics'])
        self.assertEqual(len(suppressed['suppressed']), 1)
        opaque = self.run_source(IMPORT + b'#highlighted[#unknown[$ x $]]')
        self.assertFalse(opaque['diagnostics'])
        self.assertTrue(opaque['unresolved'])

    def test_selected_root_not_silently_replaced_by_lower_priority(self):
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.copytree(self.target, cached)
        source = IMPORT + b'#highlighted[$ x $]'
        shutil.rmtree(self.target)
        report = self.run_source(source)
        self.assertTrue(report['diagnostics'])
        self.assertTrue(report['package_evidence']['source_identity'].startswith(str(self.cache) + '/'))
        shutil.copytree(OLD, self.target)
        session, _ = self.session(source)
        with self.assertRaisesRegex(InputError, 'modern text-utils variant'):
            modern.ModernHighlightAnalyzer(session)

    def test_changed_bytes_unissued_or_mutated_session_are_refused(self):
        source = IMPORT + b'#highlighted[$ x $]'
        session, context = self.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session)
        with self.assertRaises(InputError):
            modern.ModernHighlightAnalyzer(object())
        changed = replace(session._context)
        with self.assertRaises(include_context.IncludeCaptureError):
            include_context.assert_include_context(changed)
        object.__setattr__(session._graph.sources[0], 'source', b'changed')
        with self.assertRaisesRegex(InputError, 'capture changed'):
            analyzer.analyze(context.source_for('main.typ').tree, 'main.typ')
        (self.target / 'lib.typ').write_bytes((MODERN / 'lib.typ').read_bytes() + b'\n')
        session, _ = self.session(source)
        with self.assertRaisesRegex(InputError, 'modern text-utils variant'):
            modern.ModernHighlightAnalyzer(session)

    def test_forged_tree_even_with_identical_source_and_wrong_path_are_refused(self):
        source = IMPORT + b'#highlighted[plain prose]'
        session, context = self.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session)
        tree = context.source_for('main.typ').tree
        nodes = list(tree.nodes)
        index = next(i for i, node in enumerate(nodes) if node.kind == 'Text'
                     and tree.source[node.start:node.end] == b'plain prose')
        nodes[index] = replace(nodes[index], kind='Equation', display=True)
        forged = replace(tree, nodes=tuple(nodes))
        self.assertEqual(forged.source, tree.source)
        with self.assertRaisesRegex(InputError, 'exact issued source tree'):
            analyzer.analyze(forged, 'main.typ')
        with self.assertRaisesRegex(InputError, 'exact issued source tree'):
            analyzer.analyze(tree, 'other.typ')

    def test_batch_is_all_or_nothing_with_item_and_byte_limits(self):
        source = IMPORT + b'#highlighted[$ x $]'
        session, _ = self.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session)
        batch = analyzer.analyze_all()
        self.assertEqual(len(batch['by_file']), 1)
        self.assertEqual(len(batch['by_file'][0]['diagnostics']), 1)
        self.assertFalse(batch['edit_permission'] or batch['compiler_permission'])
        prefix = '{\n  "conditional_modern_highlight": '
        suffix = '\n}'
        encoded = json.dumps({'conditional_modern_highlight': batch}, ensure_ascii=False, sort_keys=True, indent=2)
        exact_bytes = len(encoded.encode('utf-8')) - len(prefix.encode()) - len(suffix.encode())
        self.assertEqual(analyzer.analyze_all(max_bytes=exact_bytes), batch)
        with self.assertRaisesRegex(InputError, 'batch report budget'):
            analyzer.analyze_all(max_bytes=exact_bytes - 1)
        for options in ({'max_items': 1}, {'max_bytes': 1}, {'max_items': True}):
            with self.subTest(options=options), self.assertRaises(InputError):
                analyzer.analyze_all(**options)
        real = modern._ModernAnalyzer.body_math
        def corrupt(engine, tree, index):
            object.__setattr__(session._graph.sources[0], 'source', b'changed')
            return real(engine, tree, index)
        with patch.object(modern._ModernAnalyzer, 'body_math', corrupt), \
                self.assertRaisesRegex(InputError, 'capture changed'):
            analyzer.analyze_all()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the reviewed Typst compiler')
    def test_owned_fixture_executes_exact_modern_modes(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        rendered = []
        for mode in ('default', 'flow', 'phrase', 'unsupported', 'named-body'):
            call = (b'#highlighted[Before $x^2$ after]' if mode == 'default' else
                    b'#highlighted(body: [Before $x^2$ after], mode: "phrase")' if mode == 'named-body' else
                    f'#highlighted(mode: "{mode}")[Before $x^2$ after]'.encode())
            source = IMPORT + b'#set page(width: 300pt, height: 250pt, margin: 30pt)\n' + call
            (self.project / 'main.typ').write_bytes(source)
            output = self.project / f'{mode}.pdf'
            run = subprocess.run(['typst', 'compile', '--root', str(self.project),
                '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                '--creation-timestamp', '0', str(self.project / 'main.typ'), str(output)],
                capture_output=True)
            if mode in ('unsupported', 'named-body'):
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse(output.exists())
            else:
                self.assertEqual(run.returncode, 0, run.stderr.decode(errors='replace'))
                rendered.append(output.read_bytes())
        self.assertEqual(rendered[0], rendered[1])
        self.assertNotEqual(rendered[1], rendered[2])


if __name__ == '__main__':
    unittest.main()
