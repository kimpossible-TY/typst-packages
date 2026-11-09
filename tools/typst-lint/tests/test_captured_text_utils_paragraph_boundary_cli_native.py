"""Private pinned-native public CLI acceptance of changed-length root boundaries.

The independently authored canonical fixture, not original malformed layout,
anchors expected final render. Embedded Libertinus 11pt establishes 1.5em as
16.5pt. No installed font work, font bytes or shared-consumer writes occur.
"""
import json
import os
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_captured_text_utils_paragraph_cli_native as public_native
import test_captured_text_utils_paragraph_native as private_native
from paragraph_boundary_cli_test_support import source_pair, assert_source_journal, assert_attributed_repair
from test_captured_text_utils_paragraph_native import digest, file_state


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
    'Opt in to private native boundary CLI with TYPST_LINT_RENDER_TESTS=1')
class NativeParagraphBoundaryCliTests(public_native.NativeCapturedTextUtilsParagraphCliTests):
    # Do not rerun the inherited capitalization-only native test cases.
    test_public_compile_repairs_lf_and_replaces_existing_pdf = None
    test_source_only_crlf_fix_noop_then_compile_to_absent_pdf_keeps_source_identity = None
    test_real_ascii_resource_refusal_keeps_old_pdf_and_committed_source = None

    compile_private_copy = private_native.NativeCapturedTextUtilsParagraphTests.compile_private_copy

    def assert_boundary_summary(self, f, report, original, expected, *, kind='mixed', changed=True):
        summary = report['source_summary'] if report['command'] == 'compile' else report
        self.assertEqual((summary['original_sha256'], summary['final_sha256']),
                         (digest(original), digest(expected)))
        self.assertEqual((summary['profile'], summary['root_grammar_version']),
                         ('captured-text-utils-paragraph-v1', 'captured-text-utils-paragraph-grammar-v1'))
        self.assertEqual(summary['modified_paths'], [f.target] if changed else [])
        assert_attributed_repair(self, summary, f.target, original, expected, kind=kind, changed=changed)
        self.assertEqual(f.source.read_bytes(), expected)
        if changed:
            manifest, = self.manifests(f, source=True)
            assert_source_journal(self, manifest, f.target, original, expected)
            self.assertEqual(manifest.parent.name, summary['journal_id'])
            self.assertEqual(self.events(manifest)[-1]['kind'], 'committed')
        else:
            self.assertIsNone(summary['journal_id'])
        return summary

    def acceptance(self, kind, newline, bom, *, present):
        original, canonical = source_pair(kind, newline, bom)
        self.assertGreater(len(canonical), len(original))
        f = self.fixture(original, present=present)
        before = file_state(f.source)
        report = self.invoke(f)
        self.assert_boundary_summary(f, report, original, canonical, kind=kind)
        actual = self.assert_publication(f, report, canonical)
        self.assertNotEqual(file_state(f.source)[2], before[2])
        expected = self.compile_private_copy('independent-canonical-' + kind,
            canonical, f.manifest.read_bytes(), f.package.read_bytes())
        canonical_base = self.base / ('independent-canonical-' + kind)
        observed = expected['dependency_paths']
        canonical_leaf = canonical_base / 'packages/local/text-utils/0.1.2'
        self.assertEqual(len(observed), 3)
        self.assertEqual(set(observed), {'main.typ', str(canonical_leaf / 'lib.typ'),
                                        str(canonical_leaf / 'typst.toml')})
        raw = b'\0'.join(os.fsencode(path) for path in observed) + b'\0'
        self.assertEqual(expected['dependency_sha256'], digest(raw))
        # Canonical fixture is compiled separately. Insertion is permitted to
        # create indentation/paragraph changes relative to malformed original.
        self.assertEqual(actual['text'], expected['text'])
        self.assertEqual(actual['boxes'], expected['boxes'])
        self.assertEqual(actual['fontspecs'], expected['fontspecs'])
        self.assertEqual(actual['raster_sha256'], expected['raster_sha256'])
        self.assertEqual(actual['raster_dimensions'], expected['raster_dimensions'])
        final_identity = file_state(f.source)
        no_op = self.invoke(f, 'fix')
        self.assert_boundary_summary(f, no_op, canonical, canonical, kind=kind, changed=False)
        self.assertFalse(no_op['compiler_invoked'])
        self.assertEqual(file_state(f.source), final_identity)
        self.assert_resources_current()
        print('PARAGRAPH_BOUNDARY_NATIVE_CANONICAL ' + json.dumps(dict(
            kind=kind, newline=repr(newline), bom=bool(bom),
            source_before_sha256=digest(original), source_after_sha256=digest(canonical),
            source_repair_passes=report['source_summary']['repair_passes'],
            canonical_pdf_sha256=expected['pdf_sha256'], actual_pdf_sha256=actual['pdf_sha256'],
            raster_sha256=actual['raster_sha256'], dependency_paths=report['package_observation']['dependency_paths']),
            sort_keys=True), flush=True)

    def test_public_mixed_lf_compiles_to_independently_authored_canonical_layout(self):
        self.acceptance('mixed', b'\n', b'', present=True)

    def test_public_initially_unmarked_crlf_bom_compiles_to_canonical_layout(self):
        self.acceptance('unmarked', b'\r\n', b'\xef\xbb\xbf', present=False)


if __name__ == '__main__':
    unittest.main()
