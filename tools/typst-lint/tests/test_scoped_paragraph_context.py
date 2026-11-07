"""Independent occurrence-aware context tests over real, immutable syntax trees.

Only public package sources and generic temporary fixture prose are read.  The
parser prepares evidence; it is deliberately poisoned while pure analysis runs.
"""
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))

import syntax_client
from syntax_client import EXECUTABLE, parse
from static_resolver import (MANNOT_NAMESPACE, SCOPED_ANNOTATIONS, TEXT_UTILS)
from typst_lint import digest
import include_context
import scoped_paragraph_context as scoped

CAPITALIZATION = 'pde.paragraph-capitalization'
SCOPE_IMPORT = b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
MARKER_IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
SHOW_IMPORT = b'#import "@local/text-utils:0.1.2": apply-paragraph-tabs\n'
STYLE = b'#set text(lang: "en")\n#show: apply-paragraph-tabs\n'
SCOPE_BODY = b'#local-tag-scope(s => [\n\n#paragraph_tab\nthis holds.\n], prefix: "fixture")\n'
CHILD = SCOPE_IMPORT + MARKER_IMPORT + SCOPE_BODY
ROOT = SHOW_IMPORT + STYLE + b'#include "child.typ"\n'
CATALOG = ({'id': CAPITALIZATION, 'grade': 'warning', 'source': 'retained-test'},)


def _all_dataclasses(value):
    """Walk immutable report values without treating source bytes as syntax."""
    if is_dataclass(value):
        yield value
        for field in fields(value):
            yield from _all_dataclasses(getattr(value, field.name))
    elif type(value) in (tuple, frozenset):
        for item in value:
            yield from _all_dataclasses(item)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ScopedParagraphContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0/src/lib.typ'
        paths = ((TEXT_UTILS, REPO / 'local/text-utils/0.1.2/lib.typ'),
                 (SCOPED_ANNOTATIONS, REPO / 'local/scoped-annotations/0.3.0/lib.typ'),
                 (MANNOT_NAMESPACE, namespace))
        cls.packages = tuple(scoped.PackageTreeEvidence(name, str(path), parse(path.read_bytes()))
                             for name, path in paths)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-scoped-context-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def capture(self, root=ROOT, child=CHILD, *, extra=None, entrypoints=None):
        sources = {'main.typ': root, 'child.typ': child}
        sources.update(extra or {})
        for path, source in sources.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source)
        seeds = tuple((path, sources[path]) for path in (entrypoints or ('main.typ',)))
        return include_context.load_include_context(self.root, seeds)

    def analyze(self, capture, *, packages=None, **kwargs):
        return scoped.analyze_scoped_paragraph_context(capture,
            package_trees=self.packages if packages is None else packages, **kwargs)

    def report(self, root=ROOT, child=CHILD, *, extra=None, entrypoints=None, packages=None,
               **kwargs):
        captured = self.capture(root, child, extra=extra, entrypoints=entrypoints)
        return self.analyze(captured, packages=packages, **kwargs)

    def capitalization(self, report):
        return tuple(proposal for proposal in report.proposals
                     if proposal.rule_id == CAPITALIZATION)

    def assert_unresolved(self, report):
        self.assertTrue(report.unresolved, 'Unknown evidence must retain an explicit obligation')
        self.assertFalse(self.capitalization(report), report)

    def test_explicit_prefix_has_conditional_source_derived_context(self):
        captured = self.capture()
        report = self.analyze(captured)
        self.assertEqual(len(report.invocations), 1)
        invocation, = report.invocations
        self.assertEqual(invocation.prefix_mode, 'literal')
        self.assertEqual(invocation.literal_prefix, 'fixture')
        self.assertEqual(invocation.scope.parameter, 's')
        self.assertEqual(invocation.scope.source.path, 'child.typ')
        self.assertEqual(invocation.scope.source.source_sha256, digest(CHILD))
        self.assertEqual(invocation.scope.source.tree_sha256,
                         captured.source_for('child.typ').identity.tree_sha256)
        self.assertEqual(invocation.binding.package, SCOPED_ANNOTATIONS)
        self.assertEqual(invocation.binding.exported_name, 'local-tag-scope')
        context, = report.contexts
        self.assertEqual(context.scope, invocation.scope)
        self.assertEqual(context.language_origin.value, 'en')
        self.assertEqual(context.language_origin.declaration.source.path, 'main.typ')
        self.assertEqual(context.show_origin.installed_binding.exported_name, 'apply-paragraph-tabs')
        self.assertEqual(context.show_origin.declaration.source.path, 'main.typ')
        proposal, = self.capitalization(report)
        self.assertEqual(CHILD[slice(*proposal.target_span)], b'this')
        self.assertEqual(proposal.paragraph.scope, invocation.scope)
        self.assertEqual(proposal.paragraph.first_word, b'this')
        self.assertTrue(proposal.paragraph.immediate_target)
        self.assertEqual(proposal.paragraph.boundary_status, 'internal-blank-source-boundary')
        self.assertEqual(proposal.status, 'conditional-informational')
        self.assertTrue(proposal.conditions)
        self.assertTrue(report.unresolved, 'A matching namespace does not capture tiptoe or runtime')
        self.assertTrue(report.effects, 'metadata/counter effects must not disappear')
        kinds = tuple(effect.kind for effect in report.effects)
        self.assertNotIn('counter-step', kinds)
        self.assertLess(kinds.index('scope-metadata'), kinds.index('callback-body'))
        self.assertEqual(tuple(effect.ordinal for effect in report.effects), tuple(range(len(kinds))))

    def test_noneligible_initial_words_cannot_be_capitalization_findings(self):
        for word in (b'This', b'THIS', b'tHiS', b'a', b'x', b'arbitrary', b'this2', b'this-word', b'this?'):
            with self.subTest(word=word):
                report = self.report(child=CHILD.replace(b'this holds.', word + b' holds.'))
                self.assertFalse(self.capitalization(report))
        self.assertFalse(self.capitalization(self.report(child=CHILD.replace(b'this holds.\n', b'this'))))

    def test_opener_only_marker_keeps_facts_without_claiming_paragraph_start(self):
        child = CHILD.replace(b'[\n\n#paragraph_tab', b'[\n#paragraph_tab')
        report = self.report(child=child)
        self.assertFalse(self.capitalization(report))
        region, = report.paragraphs
        self.assertTrue(region.immediate_target)
        self.assertEqual(region.first_word, b'this')
        self.assertIsNotNone(region.marker_binding)
        self.assertEqual(region.boundary_status, 'unresolved')
        self.assertTrue(report.effects)
        self.assertTrue(any(item.code == 'callback-entry-boundary-unresolved'
                            for item in report.unresolved))

    def test_earlier_ordinary_paragraph_and_real_internal_parbreak_own_marker_boundary(self):
        child = CHILD.replace(b'[\n\n#paragraph_tab',
                              b'[\nEarlier literal paragraph.\n\n#paragraph_tab')
        captured = self.capture(child=child)
        report = self.analyze(captured)
        proposal, = self.capitalization(report)
        region = proposal.paragraph
        self.assertEqual(region.first_word, b'this')
        self.assertEqual(region.boundary_status, 'internal-blank-source-boundary')
        tree = captured.source_for('child.typ').tree
        previous = next(item for item in report.paragraphs if item.ordinal == region.ordinal - 1)
        marker_start = tree.nodes[region.marker_hash_node].start
        parbreaks = tuple(tree.nodes[index] for index in tree.nodes[region.scope.callback_node].children
                          if tree.nodes[index].kind == 'Parbreak')
        self.assertTrue(any(previous.span[1] <= node.start < node.end <= marker_start
                            for node in parbreaks))
        self.assertGreaterEqual(child[previous.span[1]:marker_start].count(b'\n'), 2)
        self.assertFalse(child[previous.span[1]:marker_start].strip())

    def test_previous_marked_paragraph_cannot_supply_next_marker_boundary_without_blank(self):
        for separator in (b'\n', b' '):
            with self.subTest(separator=separator):
                child = CHILD.replace(b'this holds.',
                    b'this holds.' + separator + b'#paragraph_tab\ntherefore it follows.')
                report = self.report(child=child)
                self.assertFalse(any(proposal.paragraph.first_word == b'therefore'
                                     for proposal in self.capitalization(report)))
                region = next(item for item in report.paragraphs if item.first_word == b'therefore')
                self.assertEqual(region.boundary_status, 'unresolved')
                self.assertTrue(any(item.code == 'paragraph-start-boundary-unresolved'
                                    for item in report.unresolved))

    def test_root_include_and_metadata_boundaries_cannot_supply_callback_paragraph_start(self):
        child = CHILD.replace(b'[\n\n#paragraph_tab', b'[\n#paragraph_tab')
        roots = (ROOT.replace(b'#include', b'\n\n#include'),
                 ROOT.replace(b'child.typ', b'middle.typ'))
        for root in roots:
            with self.subTest(root=root):
                report = self.report(root=root, child=child,
                    extra={'middle.typ': b'An outer paragraph.\n\n#include "child.typ"\n'})
                self.assertFalse(self.capitalization(report))
                self.assertTrue(any(item.code == 'callback-entry-boundary-unresolved'
                                    for item in report.unresolved))
        metadata = CHILD.replace(b'[\n\n#paragraph_tab',
                                 b'[\n#metadata("outside-prose")\n\n#paragraph_tab')
        report = self.report(child=metadata)
        self.assertFalse(self.capitalization(report))
        self.assertTrue(any(item.code == 'callback-entry-boundary-unresolved'
                            for item in report.unresolved))

    def test_malformed_supplied_package_syntax_is_rejected_even_with_immutable_tree(self):
        malformed = parse(b'#let =')
        self.assertTrue(malformed.nodes[0].errors)
        packages = (replace(self.packages[0], tree=malformed),) + self.packages[1:]
        with self.assertRaises(scoped.EvidenceError):
            self.report(packages=packages)

    def test_reports_never_grant_an_edit_receipt_candidate_or_complete_authority(self):
        report = self.report()
        self.assertTrue(self.capitalization(report))
        forbidden = {'edit', 'edits', 'replacement', 'candidate', 'receipt', 'write_authority',
                     'complete', 'safe', 'trusted', 'runtime_equivalent'}
        for item in _all_dataclasses(report):
            self.assertFalse(forbidden.intersection(field.name for field in fields(item)),
                             type(item).__name__)
        conditions = ' '.join(condition for proposal in report.proposals
                              for condition in proposal.conditions).lower()
        for obligation in ('initializ', 'collision', 'render'):
            self.assertIn(obligation, conditions)

    def test_lf_crlf_bom_and_utf8_keep_exact_byte_spans(self):
        for ending in (b'\n', b'\r\n'):
            for bom in (b'', b'\xef\xbb\xbf'):
                with self.subTest(ending=ending, bom=bom):
                    child = bom + (SCOPE_IMPORT + MARKER_IMPORT +
                        '이전 문장.\n'.encode() + SCOPE_BODY).replace(b'\n', ending)
                    root = bom + ROOT.replace(b'\n', ending)
                    report = self.report(root, child)
                    proposal, = self.capitalization(report)
                    self.assertEqual(child[slice(*proposal.target_span)], b'this')
                    marker = proposal.paragraph.marker
                    self.assertEqual(child[slice(*marker.span)], b'paragraph_tab')
                    invocation, = report.invocations
                    self.assertTrue(child[slice(*invocation.scope.call_span)].startswith(b'local-tag-scope'))
                    self.assertEqual(invocation.scope.source.source_sha256, digest(child))
                    for span in (proposal.target_span, marker.span,
                                 invocation.scope.call_span, invocation.scope.callback_span):
                        for boundary in span:
                            child[:boundary].decode('utf-8')

    def test_callback_and_named_import_renames_retain_explicit_origins(self):
        child = CHILD.replace(b': local-tag-scope', b': local-tag-scope as own-scope')
        child = child.replace(b'#local-tag-scope(s =>', b'#own-scope(receiver =>')
        report = self.report(child=child)
        invocation, = report.invocations
        self.assertEqual(invocation.scope.parameter, 'receiver')
        self.assertEqual(invocation.binding.local_name, 'own-scope')
        self.assertEqual(invocation.binding.exported_name, 'local-tag-scope')
        self.assertEqual(invocation.binding.declaration.source.path, 'child.typ')
        self.assertTrue(self.capitalization(report))

    def test_named_marker_alias_retains_actual_export_and_declaration(self):
        child = CHILD.replace(b': paragraph_tab', b': paragraph_tab as tab')
        child = child.replace(b'#paragraph_tab', b'#tab')
        report = self.report(child=child)
        self.assertFalse(self.capitalization(report), 'Existing canonical rule cannot silently expand to aliases')
        region = next(region for region in report.paragraphs if region.marker_binding is not None)
        self.assertEqual(region.marker_binding.local_name, 'tab')
        self.assertEqual(region.marker_binding.exported_name, 'paragraph_tab')
        self.assertEqual(region.marker_binding.declaration.source.path, 'child.typ')
        self.assertEqual(child[slice(*region.marker.span)], b'tab')
        self.assertTrue(any('alias' in item.code or 'spelling' in item.reason.lower()
                            for item in report.unresolved))

    def test_default_and_explicit_auto_prefix_are_deferred_not_immediate(self):
        modes = []
        for suffix in (b'', b', prefix: auto'):
            with self.subTest(suffix=suffix):
                child = SCOPE_IMPORT + MARKER_IMPORT + SCOPE_BODY.replace(b', prefix: "fixture"', suffix)
                report = self.report(child=child)
                self.assert_unresolved(report)
                invocation, = report.invocations
                self.assertIsNone(invocation.literal_prefix)
                self.assertIn('auto', invocation.prefix_mode)
                modes.append(invocation.prefix_mode)
                self.assertIn('context', invocation.execution_status)
                effects = ' '.join(effect.kind + ' ' + effect.phase for effect in report.effects)
                self.assertIn('counter', effects)
                self.assertIn('context', effects)
                kinds = tuple(effect.kind for effect in report.effects)
                self.assertLess(kinds.index('counter-step'), kinds.index('counter-context-read'))
                self.assertLess(kinds.index('counter-context-read'), kinds.index('scope-metadata'))
                self.assertLess(kinds.index('scope-metadata'), kinds.index('callback-body'))
        self.assertEqual(tuple(modes), ('default-auto', 'explicit-auto'))

    def test_unsupported_constructor_arguments_and_bodies_never_gain_proposals(self):
        bodies = (
            b'#local-tag-scope(s => [\n#paragraph_tab\nthis holds.\n], prefix: selected)',
            b'#local-tag-scope(s => [\n#paragraph_tab\nthis holds.\n], prefix: "a" + "b")',
            b'#local-tag-scope(s => [\n#paragraph_tab\nthis holds.\n], prefix: "p", ..options)',
            b'#local-tag-scope(s => [\n#paragraph_tab\nthis holds.\n], prefix: "p", namespace: unknown())',
            b'#local-tag-scope((s, t) => [\n#paragraph_tab\nthis holds.\n], prefix: "p")',
            b'#local-tag-scope(s => { [\n#paragraph_tab\nthis holds.\n] }, prefix: "p")',
            b'#local-tag-scope(s => [#local-tag-scope(t => [\n#paragraph_tab\nthis holds.\n], prefix: "q")], prefix: "p")',
            b'#mannot-scope(s => [\n#paragraph_tab\nthis holds.\n], prefix: "p")',
            b'#module.local-tag-scope(s => [\n#paragraph_tab\nthis holds.\n], prefix: "p")',
            b'#unknown(s => [\n#paragraph_tab\nthis holds.\n], prefix: "p")',
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assert_unresolved(self.report(child=SCOPE_IMPORT + MARKER_IMPORT + body))

    def test_callback_parameter_shadows_marker_but_does_not_rebind_installed_style(self):
        report = self.report(child=CHILD.replace(b'(s =>', b'(paragraph_tab =>'))
        self.assert_unresolved(report)
        for parameter in (b'text', b'apply-paragraph-tabs'):
            with self.subTest(parameter=parameter):
                report = self.report(child=CHILD.replace(b'(s =>', b'(' + parameter + b' =>'))
                self.assertTrue(self.capitalization(report))
                self.assertEqual(report.contexts[0].show_origin.installed_binding.package, TEXT_UTILS)

    def test_parent_shadow_of_builtin_text_does_not_establish_english(self):
        for binding in (b'#let text = (..args) => []\n',
                        b'#import "unknown.typ": text\n'):
            with self.subTest(binding=binding):
                root = SHOW_IMPORT + binding + STYLE + b'#include "child.typ"\n'
                self.assert_unresolved(self.report(root=root))

    def test_same_spelling_helpers_and_marker_shadowing_do_not_inherit_identity(self):
        prefixes = (
            b'#let local-tag-scope = body => body((:))\n',
            b'#let paragraph_tab = "fake"\n',
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assert_unresolved(self.report(child=SCOPE_IMPORT + MARKER_IMPORT + prefix + SCOPE_BODY))
        fake = CHILD.replace(SCOPE_IMPORT, b'#let local-tag-scope(body, prefix: "") = body((:))\n')
        self.assert_unresolved(self.report(child=fake))

    def test_exact_package_path_version_and_bytes_are_required(self):
        for old, new in ((b'scoped-annotations:0.3.0', b'scoped-annotations:0.3.1'),
                         (b'text-utils:0.1.2', b'text-utils:0.1.3')):
            with self.subTest(version=new):
                self.assert_unresolved(self.report(child=CHILD.replace(old, new)))
        for name in (TEXT_UTILS, SCOPED_ANNOTATIONS):
            altered = tuple(replace(package, tree=parse(package.tree.source + b'\n// changed\n'))
                            if package.import_path == name else package for package in self.packages)
            with self.subTest(package=name):
                self.assert_unresolved(self.report(packages=altered))
        self.assert_unresolved(self.report(packages=()))

    def test_missing_or_changed_namespace_cannot_prove_marker_scope_identity(self):
        variants = (tuple(package for package in self.packages if package.import_path != MANNOT_NAMESPACE),
                    tuple(replace(package, tree=parse(package.tree.source + b'\n// changed\n'))
                          if package.import_path == MANNOT_NAMESPACE else package
                          for package in self.packages))
        for packages in variants:
            with self.subTest(package_count=len(packages)):
                report = self.report(packages=packages)
                self.assert_unresolved(report)
                self.assertTrue(all(invocation.binding is None or invocation.binding.identity_status !=
                                    'pinned-definition-conditional' for invocation in report.invocations))
        supplemental = scoped.PackageTreeEvidence('@preview/tiptoe:0.4.0', 'test-only-unreviewed',
                                                  parse(b'#let unrelated = 1\n'))
        report = self.report(packages=self.packages + (supplemental,))
        self.assertTrue(report.unresolved)
        self.assertTrue(all(inv.initializer_status != 'complete' for inv in report.invocations))
        source_tree = next(package.tree for package in self.packages
                           if package.import_path == SCOPED_ANNOTATIONS)
        wrong_identity = scoped.PackageTreeEvidence('@preview/tiptoe:0.4.0', 'generic-explicit-fixture',
                                                    source_tree)
        child = CHILD.replace(b'@local/scoped-annotations:0.3.0', b'@preview/tiptoe:0.4.0')
        self.assert_unresolved(self.report(child=child, packages=self.packages + (wrong_identity,)))

    def test_child_cannot_borrow_parent_lexical_imports(self):
        root = SCOPE_IMPORT + MARKER_IMPORT + ROOT
        for child in (CHILD.replace(SCOPE_IMPORT, b''), CHILD.replace(MARKER_IMPORT, b''), SCOPE_BODY):
            with self.subTest(child=child):
                report = self.report(root, child)
                self.assert_unresolved(report)
                for binding in report.bindings:
                    if binding.declaration.source.path == 'child.typ':
                        self.assertNotEqual(binding.declaration.source.path, 'main.typ')

    def test_wildcards_do_not_create_complete_namespaces_or_known_style(self):
        cases = ((ROOT.replace(SHOW_IMPORT, SHOW_IMPORT.replace(b': apply-paragraph-tabs', b': *')), CHILD),
                 (ROOT, CHILD.replace(SCOPE_IMPORT, SCOPE_IMPORT.replace(b': local-tag-scope', b': *'))),
                 (ROOT, CHILD.replace(MARKER_IMPORT, MARKER_IMPORT.replace(b': paragraph_tab', b': *'))),
                 (ROOT, SCOPE_IMPORT + MARKER_IMPORT + b'#import "unknown.typ": *\n' + SCOPE_BODY))
        for root, child in cases:
            with self.subTest(root=root, child=child):
                self.assert_unresolved(self.report(root, child))

    def test_language_requires_effective_source_english_not_configuration(self):
        no_language = ROOT.replace(b'#set text(lang: "en")\n', b'')
        for kwargs in ({}, {'config': b'[capitalization]\nlanguage = \"en\"\n'}):
            with self.subTest(config=kwargs):
                self.assert_unresolved(self.report(root=no_language, **kwargs))
        for language in (b'"ko"', b'language', b'..options', '"en-İİ"'.encode(), '"en-KK"'.encode()):
            root = ROOT.replace(b'lang: "en"', b'lang: ' + language)
            if language == b'..options':
                root = ROOT.replace(b'lang: "en"', language)
            with self.subTest(language=language):
                self.assert_unresolved(self.report(root=root))

    def test_conflicting_duplicate_language_arguments_are_typed_capture_failure(self):
        root = ROOT.replace(b'lang: "en"', b'lang: "en", lang: "ko"')
        with self.assertRaises(include_context.IncludeSyntaxError):
            self.capture(root=root)

    def test_english_tags_are_source_derived_and_case_insensitive(self):
        for language in (b'"en"', b'"EN-us"', b'"en-GB"'):
            with self.subTest(language=language):
                report = self.report(root=ROOT.replace(b'"en"', language))
                self.assertTrue(self.capitalization(report))
                self.assertEqual(report.contexts[0].language_origin.value, language[1:-1].decode())

    def test_show_transform_does_not_itself_set_language(self):
        root = SHOW_IMPORT + b'#show: apply-paragraph-tabs\n#include "child.typ"\n'
        report = self.report(root=root)
        self.assert_unresolved(report)
        self.assertIsNone(report.contexts[0].language_origin.value)
        self.assertIsNotNone(report.contexts[0].show_origin.installed_binding)

    def test_unknown_show_is_not_cleared_by_later_english_setting(self):
        root = SHOW_IMPORT + b'#show: unknown\n' + STYLE + b'#include "child.typ"\n'
        report = self.report(root=root)
        self.assert_unresolved(report)
        self.assertEqual(report.contexts[0].language_origin.value, 'en')
        self.assertGreaterEqual(len(report.contexts[0].show_chain), 2)

    def test_arbitrary_book_and_selector_shows_are_outside_contract(self):
        for show in (b'#show: book\n', b'#show: apply-math-book\n',
                     b'#show text: it => upper(it.text)\n', b'#show: body => body\n'):
            with self.subTest(show=show):
                root = ROOT.replace(b'#show: apply-paragraph-tabs\n', show)
                self.assert_unresolved(self.report(root=root))

    def test_show_installation_captures_value_before_helper_name_is_rebound(self):
        root = ROOT.replace(b'#include', b'#let apply-paragraph-tabs = body => body\n#include')
        report = self.report(root=root)
        self.assertTrue(self.capitalization(report))
        installed = report.contexts[0].show_origin.installed_binding
        self.assertEqual(installed.package, TEXT_UTILS)
        self.assertEqual(installed.exported_name, 'apply-paragraph-tabs')
        later_unknown = root.replace(b'#include', b'#show: apply-paragraph-tabs\n#include')
        self.assert_unresolved(self.report(root=later_unknown))

    def test_order_of_root_include_sites_captures_distinct_language_context(self):
        root = SHOW_IMPORT + STYLE + b'#include "child.typ"\n#set text(lang: "ko")\n#include "child.typ"\n'
        report = self.report(root=root)
        self.assertEqual(len(report.invocations), 2)
        self.assertEqual(tuple(context.language_origin.value for context in report.contexts), ('en', 'ko'))
        proposal, = self.capitalization(report)
        self.assertEqual(proposal.paragraph.scope, report.invocations[0].scope)
        self.assertNotEqual(report.invocations[0].scope.occurrence, report.invocations[1].scope.occurrence)
        self.assertEqual(report.invocations[0].scope.call_span, report.invocations[1].scope.call_span)

    def test_child_local_language_applies_inside_child_without_leaking_to_parent_sibling(self):
        root = ROOT.replace(b'child.typ', b'middle.typ') + b'#include "child.typ"\n'
        middle = b'#set text(lang: "ko")\n#include "child.typ"\n'
        report = self.report(root=root, extra={'middle.typ': middle})
        self.assertEqual(tuple(context.language_origin.value for context in report.contexts), ('ko', 'en'))
        proposal, = self.capitalization(report)
        self.assertEqual(len(proposal.paragraph.scope.occurrence.edge_chain), 1)
        self.assertEqual(proposal.paragraph.scope, report.invocations[1].scope)

    def test_later_source_styles_cannot_apply_backwards_to_prior_include(self):
        root = SHOW_IMPORT + b'#include "child.typ"\n' + STYLE
        report = self.report(root=root)
        self.assert_unresolved(report)
        self.assertIsNone(report.contexts[0].language_origin.value)

    def test_nested_literal_include_chain_transfers_style_not_lexical_imports(self):
        root = ROOT.replace(b'child.typ', b'middle.typ')
        report = self.report(root=root, extra={'middle.typ': b'#include "child.typ"\n'})
        self.assertTrue(self.capitalization(report))
        invocation, = report.invocations
        self.assertEqual(len(invocation.scope.occurrence.edge_chain), 2)
        self.assertEqual(invocation.scope.occurrence.entrypoint, 'main.typ')

    def test_conditional_stored_closure_callback_and_wrapper_includes_are_not_execution(self):
        sites = (b'#if true { include "child.typ" }\n',
                 b'#let stored = [#include "child.typ"]\n',
                 b'#let delayed = () => { include "child.typ" }\n',
                 b'#unknown(x => [#include "child.typ"])\n',
                 b'#block[#include "child.typ"]\n',
                 b'#context include "child.typ"\n')
        for site in sites:
            with self.subTest(site=site):
                report = self.report(root=SHOW_IMPORT + STYLE + site)
                self.assert_unresolved(report)

    def test_scope_wrappers_and_stored_callbacks_are_not_direct_execution(self):
        call = SCOPE_BODY.rstrip(b'\n')
        bodies = (b'#block[' + call + b']', b'#let stored = [' + call + b']',
                  b'#if true [' + call + b']', b'#context [' + call + b']',
                  b'#unknown([' + call + b'])')
        for body in bodies:
            with self.subTest(body=body):
                self.assert_unresolved(self.report(child=SCOPE_IMPORT + MARKER_IMPORT + body))

    def test_marker_immediate_target_cannot_search_forward_through_nonprose(self):
        targets = (b'$x$\nthis holds.', b'$ x $\nthis holds.', b'= Heading\nthis holds.',
                   b'/* keep */\nthis holds.', b'// keep\nthis holds.',
                   b'#box[this holds.]\nthis holds.', b'@target\nthis holds.',
                   b'"this holds."', b'\nthis holds.')
        for target in targets:
            with self.subTest(target=target):
                self.assert_unresolved(self.report(child=CHILD.replace(b'this holds.', target)))

    def test_callback_boundary_and_metadata_are_not_prose_trivia(self):
        child = SCOPE_IMPORT + MARKER_IMPORT + (
            b'#local-tag-scope(s => [\n#paragraph_tab\n], prefix: "p")\nthis holds.\n')
        report = self.report(child=child)
        self.assert_unresolved(report)
        self.assertTrue(all(not region.first_word or region.first_word != b'this'
                            for region in report.paragraphs))
        body = SCOPE_BODY.replace(b'#paragraph_tab\nthis holds.',
                                 b'#paragraph_tab\n#metadata("x")\nthis holds.')
        self.assert_unresolved(self.report(child=SCOPE_IMPORT + MARKER_IMPORT + body))

    def test_two_marked_paragraphs_have_separate_owner_and_exact_targets(self):
        body = SCOPE_BODY.replace(b'this holds.', b'this holds.\n\n#paragraph_tab\ntherefore it follows.')
        report = self.report(child=SCOPE_IMPORT + MARKER_IMPORT + body)
        proposals = self.capitalization(report)
        self.assertEqual(tuple(proposal.paragraph.first_word for proposal in proposals),
                         (b'this', b'therefore'))
        self.assertEqual(len({proposal.paragraph.ordinal for proposal in proposals}), 2)
        self.assertEqual(len({proposal.paragraph.marker.span for proposal in proposals}), 2)

    def test_later_unmarked_paragraph_proposal_requires_own_canonical_marker_binding(self):
        body = b'#local-tag-scope(s => [First paragraph.\n\nSecond paragraph.\n], prefix: "p")\n'
        report = self.report(child=SCOPE_IMPORT + MARKER_IMPORT + body)
        proposals = tuple(proposal for proposal in report.proposals
                          if proposal.rule_id == 'pde.prose-paragraph-marker')
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].paragraph.first_word, b'Second')
        self.assertTrue(proposals[0].conditions)
        for child in (SCOPE_IMPORT + body,
                      SCOPE_IMPORT + MARKER_IMPORT.replace(b': paragraph_tab', b': paragraph_tab as tab') + body,
                      SCOPE_IMPORT + MARKER_IMPORT + body.replace(b'(s =>', b'(paragraph_tab =>')):
            with self.subTest(child=child):
                report = self.report(child=child)
                self.assertFalse(report.proposals)
                self.assertTrue(any('marker-binding' in item.code for item in report.unresolved))

    def test_suppression_stays_with_exact_marker_or_is_explicitly_unsupported(self):
        directive = b'// typst-lint: ignore-next pde.paragraph-capitalization -- intentional\n'
        body = SCOPE_BODY.replace(b'#paragraph_tab', directive + b'#paragraph_tab', 1)
        body = body.replace(b'this holds.', b'this holds.\n\n#paragraph_tab\ntherefore it follows.')
        report = self.report(child=SCOPE_IMPORT + MARKER_IMPORT + body)
        proposals = self.capitalization(report)
        self.assertFalse(any(proposal.paragraph.first_word == b'this' for proposal in proposals))
        self.assertTrue(report.unresolved or any(proposal.paragraph.first_word == b'therefore'
                                               for proposal in proposals))

    def test_repeated_source_occurrences_and_entrypoints_never_share_scope_identity(self):
        root = ROOT + b'#include "child.typ"\n'
        alternate = ROOT.replace(b'"en"', b'"ko"')
        report = self.report(root=root, extra={'other.typ': alternate},
                             entrypoints=('main.typ', 'other.typ'))
        self.assertEqual(len(report.invocations), 3)
        scopes = tuple(invocation.scope for invocation in report.invocations)
        self.assertEqual(len(set(scopes)), 3)
        self.assertEqual(len({scope.occurrence.occurrence_id for scope in scopes}), 3)
        self.assertEqual(len({scope.call_span for scope in scopes}), 1)
        self.assertEqual({scope.occurrence.entrypoint for scope in scopes}, {'main.typ', 'other.typ'})
        self.assertEqual(len(self.capitalization(report)), 2)
        self.assertTrue(any('collision' in obligation.code or 'collision' in obligation.reason.lower()
                            for obligation in report.unresolved))

    def test_repeated_literal_prefix_does_not_claim_counter_or_collision_resolution(self):
        child = SCOPE_IMPORT + MARKER_IMPORT + SCOPE_BODY + SCOPE_BODY
        report = self.report(child=child)
        self.assertEqual(len(report.invocations), 2)
        self.assertTrue(any('collision' in obligation.code or 'collision' in obligation.reason.lower()
                            for obligation in report.unresolved))
        auto_child = child + SCOPE_BODY.replace(b'prefix: "fixture"', b'prefix: auto')
        report = self.report(child=auto_child)
        self.assertTrue(report.unresolved)
        self.assertTrue(all(proposal.conditions for proposal in report.proposals))

    def test_report_is_frozen_deterministic_and_does_not_mutate_inputs(self):
        captured = self.capture()
        before = copy.deepcopy(captured)
        packages = copy.deepcopy(self.packages)
        first = self.analyze(captured)
        self.assertEqual(first, self.analyze(captured))
        self.assertEqual(captured, before)
        self.assertEqual(self.packages, packages)
        for item in _all_dataclasses(first):
            self.assertTrue(type(item).__dataclass_params__.frozen)
            if fields(item):
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    setattr(item, fields(item)[0].name, None)

    def test_pure_analysis_cannot_parse_read_resolve_certify_compile_or_write(self):
        import capitalization
        import edit_plan
        import facade_provenance
        import package_loader
        import paragraph_rules
        import typst_lint
        import static_resolver
        captured = self.capture()
        expected = self.analyze(captured)
        no = AssertionError('The occurrence analyzer must consume only retained immutable evidence')
        with ExitStack() as stack:
            for target in ('builtins.open', 'os.open', 'os.stat', 'os.listdir', 'os.scandir',
                           'os.walk', 'subprocess.Popen', 'subprocess.run'):
                stack.enter_context(patch(target, side_effect=no))
            for target, name in ((Path, 'open'), (Path, 'read_bytes'), (Path, 'write_bytes'),
                                 (Path, 'read_text'), (Path, 'write_text'), (Path, 'home'),
                                 (syntax_client, 'parse'), (include_context, 'parse'),
                                 (package_loader, 'load_packages'), (static_resolver, 'resolve'),
                                 (facade_provenance, 'certify_initializer'),
                                 (typst_lint, 'load_rules'), (edit_plan, 'plan'),
                                 (paragraph_rules, 'analyze'), (capitalization, 'evaluate')):
                stack.enter_context(patch.object(target, name, side_effect=no))
            self.assertEqual(self.analyze(captured), expected)

    def test_existing_nested_resolver_remains_conservative_and_unchanged(self):
        import static_resolver
        captured = self.capture()
        tree = captured.source_for('child.typ').tree
        packages = tuple(static_resolver.PackageSource(item.import_path, item.source_identity,
                                                       item.tree.source) for item in self.packages)
        before = static_resolver.resolve(tree, packages=packages)
        report = self.analyze(captured)
        self.assertTrue(self.capitalization(report))
        self.assertEqual(static_resolver.resolve(tree, packages=packages), before)
        text_ids = tuple(index for index, node in enumerate(tree.nodes)
                         if node.kind == 'Text' and tree.source[node.start:node.end].startswith(b'this '))
        self.assertTrue(text_ids)
        self.assertTrue(all(not before.facts[index].prose_safe for index in text_ids))

    def test_captured_cycle_and_missing_literal_target_stay_explicitly_incomplete(self):
        report = self.report(child=CHILD + b'#include "main.typ"\n')
        self.assertTrue(any('cycle' in item.code for item in report.unresolved))
        self.assertTrue(all(proposal.conditions for proposal in report.proposals))
        report = self.report(root=ROOT + b'#include "absent.typ"\n')
        self.assertTrue(any('missing' in item.code for item in report.unresolved))
        self.assertTrue(all(proposal.conditions for proposal in report.proposals))

    def test_retained_inputs_survive_files_changed_after_capture(self):
        captured = self.capture()
        expected = self.analyze(captured)
        for source in captured.sources:
            (self.root / source.path).write_bytes(b'Changed after capture.\n')
        self.assertEqual(self.analyze(captured), expected)

    def test_explicit_catalog_and_config_are_copied_without_ambient_authority(self):
        config = b'[capitalization]\nlanguage = \"ko\"\n'
        report = self.report(config=config, catalog=CATALOG)
        self.assertTrue(self.capitalization(report), 'Source language overrides config editorial hint')
        self.assertEqual(report.config_sha256, digest(config))
        self.assertIn((CAPITALIZATION, 'warning', 'retained-test'), report.catalog_records)
        self.assertEqual(report.contexts[0].language_origin.value, 'en')

    def test_empty_invalid_or_duplicate_catalog_is_not_an_ambient_fallback(self):
        captured = self.capture()
        self.assertEqual(self.analyze(captured, catalog=()).catalog_records, ())
        for supplied in ({}, (None,), ({'id': CAPITALIZATION},), CATALOG + CATALOG):
            with self.subTest(catalog=supplied), self.assertRaises(scoped.EvidenceError):
                self.analyze(captured, catalog=supplied)

    def test_malformed_top_level_inputs_are_typed_bounded_failures(self):
        captured = self.capture()
        for malformed in (None, (), {}, replace(captured, sources=[]),
                          replace(captured, occurrences=[]), replace(captured, edges=[])):
            with self.subTest(value=type(malformed).__name__), self.assertRaises(scoped.EvidenceError):
                self.analyze(malformed)
        for malformed in ([], (None,), self.packages + self.packages[:1]):
            with self.subTest(packages=malformed), self.assertRaises(scoped.EvidenceError):
                self.analyze(captured, packages=malformed)

    def test_forged_source_hash_tree_hash_and_raw_bytes_are_rejected(self):
        captured = self.capture()
        item = captured.source_for('child.typ')
        for forged in (replace(item, identity=replace(item.identity, source_sha256='0' * 64)),
                       replace(item, identity=replace(item.identity, tree_sha256='0' * 64)),
                       replace(item, source=item.source + b' '),
                       replace(item, tree=replace(item.tree, source=item.source + b' '))):
            sources = tuple(forged if source.path == item.path else source for source in captured.sources)
            with self.subTest(forged=forged.identity), self.assertRaises(scoped.EvidenceError):
                self.analyze(replace(captured, sources=sources))

    def test_forged_include_span_ancestry_or_missing_target_is_rejected(self):
        captured = self.capture()
        edge, = captured.edges
        variants = (replace(edge, node=replace(edge.node, start=edge.node.start + 1)),
                    replace(edge, ancestry=()), replace(edge, target=None),
                    replace(edge, node=replace(edge.node, node_id=0)),
                    replace(edge, ancestry=edge.ancestry + (edge.node,)))
        for forged in variants:
            with self.subTest(edge=forged), self.assertRaises(scoped.EvidenceError):
                self.analyze(replace(captured, edges=(forged,)))
        with self.assertRaises(scoped.EvidenceError):
            self.analyze(replace(captured, sources=tuple(source for source in captured.sources
                                                       if source.path != 'child.typ')))

    def test_forged_occurrence_chain_or_entrypoint_is_rejected(self):
        captured = self.capture()
        child = next(occurrence for occurrence in captured.occurrences if occurrence.edge_chain)
        main = next(occurrence for occurrence in captured.occurrences if not occurrence.edge_chain)
        variants = (replace(child, edge_chain=()), replace(child, entrypoint=child.source),
                    replace(child, edge_chain=child.edge_chain * 2),
                    replace(child, source=main.source))
        for forged in variants:
            occurrences = tuple(forged if occurrence is child else occurrence
                                for occurrence in captured.occurrences)
            with self.subTest(occurrence=forged), self.assertRaises(scoped.EvidenceError):
                self.analyze(replace(captured, occurrences=occurrences))

    def test_full_tree_node_corruption_cycles_types_and_spans_are_rejected(self):
        captured = self.capture()
        item = captured.source_for('child.typ')
        root_node = item.tree.nodes[0]
        variants = (replace(root_node, children=(0,)), replace(root_node, children=[]),
                    replace(root_node, start=True), replace(root_node, end=len(item.source) + 1))
        for node in variants:
            nodes = (node,) + item.tree.nodes[1:]
            forged = replace(item, tree=replace(item.tree, nodes=nodes))
            sources = tuple(forged if source.path == item.path else source for source in captured.sources)
            with self.subTest(node=node), self.assertRaises(scoped.EvidenceError):
                self.analyze(replace(captured, sources=sources))

    def test_occurrence_weighted_byte_budget_has_exact_finite_boundary(self):
        captured = self.capture(root=ROOT + b'#include "child.typ"\n')
        expected = self.analyze(captured)
        required = sum(len(captured.source_for(occurrence.source.path).source)
                       for occurrence in captured.occurrences)
        self.assertGreater(required, sum(len(source.source) for source in captured.sources))
        with patch.object(scoped, 'MAX_ANALYZED_BYTES', required):
            self.assertEqual(self.analyze(captured), expected)
        with patch.object(scoped, 'MAX_ANALYZED_BYTES', required - 1):
            with self.assertRaises(scoped.EvidenceError):
                self.analyze(captured)

    def test_show_chain_budget_has_exact_finite_boundary(self):
        root = ROOT.replace(b'#show: apply-paragraph-tabs\n', b'#show: apply-paragraph-tabs\n' * 2)
        captured = self.capture(root=root)
        expected = self.analyze(captured)
        self.assertEqual(len(expected.contexts[0].show_chain), 2)
        with patch.object(scoped, 'MAX_SHOW_CHAIN', 2):
            self.assertEqual(self.analyze(captured), expected)
        with patch.object(scoped, 'MAX_SHOW_CHAIN', 1):
            with self.assertRaises(scoped.EvidenceError):
                self.analyze(captured)

    def test_uninitialized_or_deleted_evidence_fields_fail_through_typed_boundary(self):
        captured = self.capture()
        uninitialized_tree = object.__new__(syntax_client.Tree)
        uninitialized_package = object.__new__(scoped.PackageTreeEvidence)
        deleted = replace(self.packages[0])
        object.__delattr__(deleted, 'import_path')
        variants = (replace(self.packages[0], tree=uninitialized_tree),
                    uninitialized_package, deleted)
        for ordinal, package in enumerate(variants):
            with self.subTest(ordinal=ordinal), self.assertRaises(scoped.EvidenceError):
                self.analyze(captured, packages=(package,))

    def test_malformed_catalog_cannot_invoke_user_defined_callbacks(self):
        class CallbackMapping(Mapping):
            def __getitem__(self, key):
                raise AssertionError('No callback from retained evidence')
            def __iter__(self):
                raise AssertionError('No callback from retained evidence')
            def __len__(self):
                raise AssertionError('No callback from retained evidence')
            def get(self, *args):
                raise AssertionError('No callback from retained evidence')

        class CallbackDict(dict):
            def get(self, *args):
                raise AssertionError('No callback from retained evidence')

        class CallbackKey:
            def __hash__(self):
                return hash('id')
            def __eq__(self, other):
                raise AssertionError('No callback from retained evidence')

        captured = self.capture()
        for record in (CallbackMapping(), CallbackDict(CATALOG[0]), {CallbackKey(): 'bad'}):
            with self.subTest(kind=type(record).__name__), self.assertRaises(scoped.EvidenceError):
                self.analyze(captured, catalog=(record,))

    def test_retained_catalog_records_do_not_keep_mutable_source_objects(self):
        catalog = [dict(CATALOG[0])]
        report = self.report(catalog=catalog)
        original = report.catalog_records
        catalog[0]['grade'] = 'changed later'
        catalog.append(dict(CATALOG[0], id='unrelated'))
        self.assertEqual(report.catalog_records, original)

    def test_in_place_frozen_bypass_cannot_replace_original_parser_facts(self):
        captured = self.capture()
        tree = captured.source_for('child.typ').tree
        original = tree.nodes
        index = next(index for index, node in enumerate(original) if node.kind == 'Ident')
        forged = replace(original[index], kind='Text')
        try:
            object.__setattr__(tree, 'nodes', original[:index] + (forged,) + original[index + 1:])
            with self.assertRaises(scoped.EvidenceError):
                self.analyze(captured)
        finally:
            object.__setattr__(tree, 'nodes', original)
        self.assertTrue(self.capitalization(self.analyze(captured)))

    def test_fixed_analysis_limits_fail_typed_instead_of_truncating(self):
        captured = self.capture()
        for limit in ('MAX_SOURCES', 'MAX_OCCURRENCES', 'MAX_EDGES', 'MAX_NODES', 'MAX_TOTAL_BYTES',
                      'MAX_PACKAGES', 'MAX_PACKAGE_NODES', 'MAX_ANALYZED_NODES',
                      'MAX_ANALYZED_BYTES', 'MAX_SHOW_CHAIN', 'MAX_FACTS'):
            with self.subTest(limit=limit), patch.object(scoped, limit, 0):
                with self.assertRaises(scoped.EvidenceError):
                    self.analyze(captured)


if __name__ == '__main__':
    unittest.main()
