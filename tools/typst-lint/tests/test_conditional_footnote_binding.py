"""Captured facade absence never grants compilation or write authority."""
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding as binding
import include_context
import namespace_multiroot
import namespace_packages
from syntax_client import EXECUTABLE
from typst_lint import InputError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalFootnoteBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (self.base / name for name in ('project', 'packages', 'cache'))
        for directory in (self.project, self.packages, self.cache):
            directory.mkdir()
        fixture = TOOL / 'tests/fixtures/reviewed-packages/local'
        for name, version in (('math-blocks', '0.2.0'), ('text-utils', '0.1.2')):
            shutil.copytree(fixture / name / version, self.packages / 'local' / name / version)

    def session(self, body, facade=b'#import "@local/text-utils:0.1.2": *\n'):
        (self.project / 'styles.typ').write_bytes(facade)
        (self.project / 'main.typ').write_bytes(body)
        context = include_context.load_include_context(self.project, (('main.typ', body),))
        namespaces = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        return binding.ConditionalFootnoteBinding(context, namespaces)

    def test_facade_absence_yields_exact_source_bound_conditional_call(self):
        body = b'#import "styles.typ": *\n#block[$\n x #footnote[Note]\n$]'
        result = self.session(body).analyze('main.typ')
        self.assertEqual(result.status, 'CONDITIONAL', result.unresolved)
        self.assertEqual(len(result.calls), 1)
        call = result.calls[0]
        self.assertEqual(body[call.start:call.end], b'#footnote[Note]')
        self.assertEqual(call.source_sha256, binding.digest(body))
        self.assertEqual([x.path for x in call.facade_sources], ['styles.typ'])
        self.assertEqual([x.import_path for x in call.package_evidence], ['@local/text-utils:0.1.2'])
        self.assertTrue(call.conditions)
        self.assertFalse(call.edit_permission or call.compiler_permission or result.edit_permission)

    def test_facade_export_and_preceding_or_following_shadows_are_not_promoted(self):
        use = b'#block[$\n x #footnote[Note]\n$]'
        cases = (
            (b'#import "styles.typ": *\n' + use, b'#let footnote = none\n'),
            (b'#let footnote = none\n#import "styles.typ": *\n' + use, b''),
            (b'#import "styles.typ": *\n#let footnote = none\n' + use, b''),
            (b'#import "styles.typ": *\n#import "unknown.typ": *\n' + use, b''),
            (b'#import "unknown.typ": *\n#import "styles.typ": *\n' + use, b''),
            (b'#import "styles.typ": *\n#{footnote = none}\n' + use, b''),
            (b'#import "styles.typ" as footnote: *\n' + use, b''),
        )
        for source, facade in cases:
            with self.subTest(source=source, facade=facade):
                self.assertFalse(self.session(source, facade).analyze('main.typ').calls)

    def test_dynamic_or_incomplete_facade_is_not_a_negative_export_proof(self):
        source = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        for facade in (b'#import path: *\n', b'#import "missing.typ": *\n',
                       b'#include "other.typ"\n', b'#import "styles.typ": *\n'):
            with self.subTest(facade=facade):
                result = self.session(source, facade).analyze('main.typ')
                self.assertFalse(result.calls)
                self.assertTrue(result.unresolved)

    def test_forged_changed_and_wrong_source_captures_fail_closed(self):
        source = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        session = self.session(source)
        self.assertEqual(session.analyze('other.typ').status, 'UNAVAILABLE')
        with self.assertRaises(InputError):
            binding.ConditionalFootnoteBinding(session._context, replace(session._namespaces))
        forged = object.__new__(binding.ConditionalFootnoteBinding)
        self.assertEqual(forged.analyze('main.typ').status, 'UNAVAILABLE')
        object.__setattr__(session._graph.sources[0], 'source', b'changed')
        self.assertEqual(session.analyze('main.typ').status, 'UNAVAILABLE')

    def test_cache_selection_requires_actual_first_root_absence(self):
        first = self.packages / 'local/text-utils/0.1.2'
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.copytree(first, cached)
        shutil.rmtree(first)
        source = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        result = self.session(source).analyze('main.typ')
        self.assertEqual(len(result.calls), 1, result.unresolved)
        self.assertTrue(result.calls[0].package_evidence[0].source_identity.startswith(str(self.cache) + '/'))
        first.mkdir(parents=True)
        (first / 'typst.toml').write_text('not the reviewed manifest')
        (first / 'lib.typ').write_text('#let footnote = none')
        self.assertFalse(self.session(source).analyze('main.typ').calls)

    def test_wildcard_evidence_does_not_escape_its_lexical_body(self):
        source = (b'#block[\n#import "styles.typ": *\n#footnote[inside]\n]\n'
                  b'#footnote[outside]\n')
        result = self.session(source).analyze('main.typ')
        self.assertEqual(len(result.calls), 1)
        call = result.calls[0]
        self.assertEqual(source[call.start:call.end], b'#footnote[inside]')

    def test_instance_method_substitution_cannot_issue_proof(self):
        source = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        session = self.session(source, b'#let footnote = none\n')
        session._analyze = lambda path: binding.BindingResult('CONDITIONAL', calls=('forged',))
        self.assertFalse(session.analyze('main.typ').calls)

    def test_collision_only_namespace_proves_names_not_helper_origins(self):
        synthetic = b'#let highlighted(body) = body\n#let paragraph_tab = none\n'
        (self.packages / 'local/text-utils/0.1.2/lib.typ').write_bytes(synthetic)
        variant = replace(namespace_packages._TEXT_COLLISION_CONTRACT,
                          source_sha256=hashlib.sha256(synthetic).hexdigest())
        source = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        with patch.object(namespace_packages, '_TEXT_COLLISION_CONTRACT', variant):
            result = self.session(source).analyze('main.typ')
            self.assertEqual(len(result.calls), 1, result.unresolved)
            self.assertFalse(result.calls[0].edit_permission or result.calls[0].compiler_permission)

    def test_batch_matches_single_results_and_checks_capture_only_twice(self):
        chapter = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        (self.project / 'chapter.typ').write_bytes(chapter)
        session = self.session(b'#include "chapter.typ"\n')
        expected = tuple((source.path, session.analyze(source.path)) for source in session._context.sources)
        with patch.object(binding, '_valid', wraps=binding._valid) as valid:
            batch = session.analyze_all(max_items=10000)
        self.assertEqual(batch.results, expected)
        self.assertEqual(valid.call_count, 2)
        self.assertFalse(batch.edit_permission or batch.compiler_permission)

    def test_batch_discards_every_result_if_capture_changes_or_analysis_fails(self):
        (self.project / 'chapter.typ').write_bytes(b'#import "styles.typ": *\n#footnote[Chapter]')
        for mutation, fail in ((True, False), (True, True), (False, True)):
            with self.subTest(mutation=mutation, fail=fail):
                session = self.session(b'#include "chapter.typ"\n')
                original = binding.ConditionalFootnoteBinding._analyze
                count = []
                def intercept(instance, path):
                    result = original(instance, path)
                    count.append(path)
                    if len(count) == 2:
                        if mutation:
                            object.__setattr__(session._graph.sources[0], 'source', b'changed')
                        if fail:
                            raise RuntimeError('fixture failure')
                    return result
                with patch.object(binding.ConditionalFootnoteBinding, '_analyze', intercept):
                    if not mutation:
                        with self.assertRaises(RuntimeError):
                            session.analyze_all(max_items=10000)
                    else:
                        batch = session.analyze_all(max_items=10000)
                        self.assertEqual(batch.status, 'UNAVAILABLE')
                        self.assertFalse(batch.results)

    def test_batch_enforces_exact_aggregate_limit_before_return(self):
        source = b'#import "styles.typ": *\n#footnote[Note]\n'
        session = self.session(source)
        row = session.analyze('main.typ')
        size = 1 + len(row.unresolved) + sum(1 + len(call.facade_sources)
            + len(call.package_evidence) + len(call.package_requests) + len(call.conditions)
            for call in row.calls)
        self.assertEqual(len(session.analyze_all(max_items=size).results), 1)
        with self.assertRaises(InputError):
            session.analyze_all(max_items=size - 1)
        for invalid in (True, -1, 1.0, '3'):
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                session.analyze_all(max_items=invalid)

    def test_changed_session_during_inspection_discards_even_on_exception(self):
        source = b'#import "styles.typ": *\n#block[$ x #footnote[Note] $]'
        for fail in (False, True):
            with self.subTest(fail=fail):
                session = self.session(source)
                original = binding._FootnoteResolver.visit
                changed = []
                def mutate(resolver, *args, **kwargs):
                    result = original(resolver, *args, **kwargs)
                    if not changed:
                        changed.append(True)
                        object.__setattr__(session._graph.sources[0], 'source', b'changed')
                        if fail:
                            raise RuntimeError('test exception')
                    return result
                with patch.object(binding._FootnoteResolver, 'visit', mutate):
                    result = session.analyze('main.typ')
                self.assertEqual(result.status, 'UNAVAILABLE')
                self.assertFalse(result.calls)


if __name__ == '__main__':
    unittest.main()
