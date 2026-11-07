"""Independent real-parser/loader/CLI checks for captured local facade access.

Only temporary consumers and copies of the reviewed repository packages are
written. Candidate histories come from actual analysis; no parser flags, graph
completeness claims, or successful repair histories are manufactured here.
"""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
import import_graph
import package_loader
import typst_lint
from audit_rules import candidate_verdict
from convergence_evidence import replay_evidence
from edit_plan import Edit, plan
from footnote_rules import GAP
from highlight_rules import RULE_ID as HIGHLIGHT
from import_graph import GraphAnalyzer, ImportGraphError, load_import_graph
from import_rules import ORDER, REDUNDANT
from paragraph_rules import CAPITALIZATION
from prose_marker_rules import RULE_ID as MARKER, analyze as markers
from source_analysis import analyze, rule_ids
from static_resolver import TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, report

STYLES = (b'#import "@local/text-utils:0.1.2": paragraph-tab as marker, '
          b'apply-paragraph-tabs as tabs\n'
          b'#let saved-marker = marker\n#let count = 1\n')
IMPORT = b'#import "styles.typ": saved-marker as paragraph_tab, tabs as apply-paragraph-tabs\n'
DIRECT = b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
SETUP = b'#show: apply-paragraph-tabs\n#set text(lang: "en")\n'
BODY = b'First idea.\n\nthis consequence follows.\n'
SOURCE = IMPORT + SETUP + BODY
SCRIPT = 'core.parenthesized-scripts'


def proposed(source, value):
    return plan(source, (Edit(e['start'], e['end'], e['replacement'].encode('utf-8'))
                         for e in value['proposed_edits'])).candidate()


def owners(value):
    return {rid for e in value['proposed_edits'] for rid in e.get('rule_ids', [e['rule_id']])}


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class FacadeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-facade-integration-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root, self.package_root, self.cache = (self.base / p for p in ('consumer', 'packages', 'cache'))
        self.root.mkdir()
        self.cache.mkdir()
        self.package_bytes = {}
        self.copy_package('text-utils', '0.1.2')
        self.target = self.put('main.typ', SOURCE)
        self.styles = self.put('styles.typ', STYLES)
        self.config = self.put('.typst-lint.toml', b'rule-set="pde"\ninclude=["main.typ"]\n')

    def put(self, name, source):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def copy_package(self, name, version):
        for filename in ('typst.toml', 'lib.typ'):
            relative = Path('local') / name / version / filename
            raw = (REPO / relative).read_bytes()
            target = self.package_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            self.package_bytes[relative] = raw

    def capture(self, source=SOURCE):
        self.target.write_bytes(source)
        graph = load_import_graph(self.root, (('main.typ', source),))
        loaded = package_loader.load_packages(self.package_root, (TEXT_UTILS,))
        self.assertFalse(loaded.unavailable)
        self.assertEqual(len(loaded.sources), 1)
        self.assertEqual(digest(loaded.sources[0].source), TEXT_UTILS_SOURCE_SHA256)
        return graph, loaded.sources

    def raw_marker(self, source=SOURCE, *, graph=None, packages=None, path='main.typ'):
        if graph is None or packages is None:
            captured, loaded = self.capture(source)
            graph = captured if graph is None else graph
            packages = loaded if packages is None else packages
        tree = parse(source)
        resolution = resolve(tree, packages=packages, import_graph=graph, source_path=path)
        return markers(tree, path, resolution, allow_followup_casing=True)

    def pair(self, source=SOURCE):
        self.target.write_bytes(source)
        contexts = {}
        before = report(self.root, 'dry-run', package_path=self.package_root,
                        _analysis_observer=contexts.__setitem__)
        candidate = proposed(source, before)
        self.assertEqual(self.target.read_bytes(), source)
        self.target.write_bytes(candidate)  # Only this test-owned consumer.
        after = report(self.root, 'check', package_path=self.package_root)
        return before, after, candidate, contexts

    def assert_verified(self, source, before, after, candidate, contexts):
        verdict = candidate_verdict(before, after, {'main.typ': source.decode('utf-8')},
                                    {'main.typ': candidate.decode('utf-8')},
                                    analysis_contexts=contexts)
        self.assertTrue(verdict['verified'], verdict)
        self.assertFalse(after['proposed_edits'])
        self.assertTrue(all(e['baseline_sha256'] == digest(source) for e in before['proposed_edits']))
        self.assertFalse(before['compiler_invoked'] or before['applied_fixes'] or before['coverage']['complete'])
        return verdict

    def test_package_requested_only_inside_captured_facade_is_loaded_once(self):
        contexts = {}
        original = package_loader.load_packages
        with patch.object(package_loader, 'load_packages', wraps=original) as load:
            value = report(self.root, 'dry-run', package_path=self.package_root,
                           _analysis_observer=contexts.__setitem__)
        self.assertEqual(load.call_count, 1)
        self.assertEqual({p for p in load.call_args.args[1] if p.startswith('@')}, {TEXT_UTILS})
        evidence, = value['package_evidence']
        self.assertEqual((evidence['import_path'], evidence['source_sha256']),
                         (TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256))
        self.assertFalse(any(p['import_path'] == TEXT_UTILS for p in value['package_unavailable']))
        self.assertEqual([i['path'] for i in value['inputs']], ['main.typ'])
        self.assertEqual({row['path'] for row in value['coverage']['files']}, {'main.typ'})
        context = contexts['main.typ']
        self.assertEqual(context.import_graph.selected, ('main.typ',))
        self.assertEqual({s.path for s in context.import_graph.sources}, {'main.typ', 'styles.typ'})
        self.assertEqual(proposed(SOURCE, value), SOURCE.replace(b'this consequence', b'#paragraph_tab\nThis consequence'))

    def test_marker_then_capitalization_uses_real_two_pass_history_and_independent_audit(self):
        before, after, candidate, contexts = self.pair()
        self.assertEqual(candidate, SOURCE.replace(b'this consequence', b'#paragraph_tab\nThis consequence'))
        self.assertEqual(owners(before), {MARKER, CAPITALIZATION})
        record, = before['convergence_evidence']
        self.assertEqual(record['passes'], 2)
        first, second = record['steps']
        self.assertEqual({rid for e in first['applied'] for rid in e['rule_ids']}, {MARKER})
        self.assertEqual({rid for e in second['applied'] for rid in e['rule_ids']}, {CAPITALIZATION})
        self.assertTrue(all(e['start'] == e['end'] for e in first['applied']))
        self.assertEqual(second['applied'][0]['replacement'], 'T')
        self.assert_verified(SOURCE, before, after, candidate, contexts)

    def test_footnote_boundary_and_lowercase_chain_keep_separate_real_owners(self):
        source = IMPORT + SETUP + b'First ends.#footnote[Reason.]\n\nthis consequence follows.\n'
        before, after, candidate, contexts = self.pair(source)
        expected = source.replace(b']\n\n', b']\n#parbreak()\n\n').replace(
            b'this consequence', b'#paragraph_tab\nThis consequence')
        self.assertEqual(candidate, expected)
        self.assertEqual(owners(before), {MARKER, CAPITALIZATION})
        record, = before['convergence_evidence']
        self.assertEqual(record['passes'], 2)
        self.assertEqual(len(record['steps'][0]['applied']), 2)
        self.assertNotIn(GAP, {rid for e in record['steps'][0]['applied'] for rid in e['rule_ids']})
        self.assertTrue(any(d['rule_id'] == GAP for d in before['diagnostics']))
        self.assert_verified(source, before, after, candidate, contexts)

    def test_reviewed_highlight_helper_retains_identity_through_facade_aliases(self):
        self.styles.write_bytes(STYLES + b'#import "@local/text-utils:0.1.2": highlighted as paint\n')
        source = b'#import "styles.typ": paint as hi\n#hi[A $x$ B]\n'
        before, after, candidate, contexts = self.pair(source)
        self.assertEqual(candidate, source.replace(b'#hi[A $x$ B]', b'#hi[A ]$x$#hi[ B]'))
        self.assertEqual(owners(before), {HIGHLIGHT})
        self.assert_verified(source, before, after, candidate, contexts)

    def test_direct_imports_remain_supported_but_alias_p_does_not_invent_marker_spelling(self):
        direct = DIRECT + SETUP + BODY
        before, after, candidate, contexts = self.pair(direct)
        self.assertEqual(candidate, direct.replace(b'this consequence', b'#paragraph_tab\nThis consequence'))
        self.assert_verified(direct, before, after, candidate, contexts)
        aliased = direct.replace(b': paragraph_tab,', b': paragraph_tab as p,')
        self.target.write_bytes(aliased)
        value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertNotIn(MARKER, owners(value))
        self.assertNotIn(b'#paragraph_tab\n', proposed(aliased, value))

    def test_consumer_refusals_do_not_leave_a_partially_trusted_named_import(self):
        cases = (
            IMPORT.replace(b'tabs as apply-paragraph-tabs', b'missing as apply-paragraph-tabs'),
            IMPORT.replace(b'saved-marker as paragraph_tab', b'missing as paragraph_tab'),
            IMPORT.replace(b'saved-marker as paragraph_tab, tabs as apply-paragraph-tabs',
                           b'tabs as paragraph_tab, saved-marker as apply-paragraph-tabs'),
            b'#import "styles.typ": *\n',
            b'#import "styles.typ" as styles\n',
            b'#import "styles.typ": saved-marker.value as paragraph_tab, tabs as apply-paragraph-tabs\n',
        )
        for incoming in cases:
            with self.subTest(incoming=incoming):
                source = incoming + SETUP + BODY
                graph, packages = self.capture(source)
                result = self.raw_marker(source, graph=graph, packages=packages)
                self.assertFalse(result.edits)
                tree = parse(source + b'#paragraph_tab\n')
                # Use a fresh authentic capture so the assertion is about the
                # complete named import, not merely a deliberately stale tree.
                graph, packages = self.capture(tree.source)
                resolution = resolve(tree, packages=packages, import_graph=graph, source_path='main.typ')
                last = max(i for i, n in enumerate(tree.nodes) if n.kind == 'Ident'
                           and tree.source[n.start:n.end] == b'paragraph_tab')
                binding = resolution.facts[last].binding
                self.assertFalse(binding and binding.name == 'paragraph_tab' and binding.package == TEXT_UTILS)

    def test_consumer_rebinding_does_not_preserve_helper_or_marker_identity(self):
        for binding in (b'#let paragraph_tab = "plain"\n',
                        b'#let apply-paragraph-tabs(body) = body\n',
                        b'#paragraph_tab = "plain"\n'):
            with self.subTest(binding=binding):
                source = IMPORT + binding + SETUP + BODY
                self.assertFalse(self.raw_marker(source).edits)

    def test_unaccounted_or_effectful_facade_initializers_refuse_even_when_unused(self):
        rejected = (
            STYLES + b'#let unused = unknown\n',
            STYLES + b'#let unused = count()\n',
            STYLES + b'#let unused(x) = x\n',
            STYLES + b'#let unused = [content]\n',
            STYLES + b'#let (a, b) = (1, 2)\n',
            STYLES + b'#include "empty.typ"\n',
            STYLES + b'#set text(lang: "ko")\n',
            STYLES + b'#show: identity\n',
            STYLES + b'#for x in (1,) {}\n',
            STYLES + b'#count = 2\n',
            STYLES + b'Unused output.\n',
            STYLES + b'#let count = 2\n',
            STYLES + b'#import "@local/text-utils:0.1.2": paragraph_tab as marker\n',
            b'#let saved-marker = marker\n' + STYLES,
            STYLES.replace(b'paragraph-tab as marker, apply-paragraph-tabs as tabs', b'*'),
            b'#import "@local/text-utils:0.1.2" as t\n' + STYLES,
            b'#import "@local/text-utils:0.1.2": paragraph_tab.value as unused\n' + STYLES,
        )
        self.put('empty.typ', b'')
        for facade in rejected:
            with self.subTest(facade=facade):
                self.styles.write_bytes(facade)
                self.assertFalse(self.raw_marker().edits)

    def test_named_ordinary_literal_export_shadows_builtin_text(self):
        source = IMPORT.replace(b'tabs as apply-paragraph-tabs', b'tabs as apply-paragraph-tabs, count as text') + SETUP + BODY
        graph, packages = self.capture(source)
        tree = parse(source)
        resolution = resolve(tree, packages=packages, import_graph=graph, source_path='main.typ')
        index = max(i for i, node in enumerate(tree.nodes) if node.kind == 'Ident'
                    and tree.source[node.start:node.end] == b'text')
        self.assertIsNone(resolution.facts[index].binding)
        self.assertFalse(markers(tree, 'main.typ', resolution, allow_followup_casing=True).edits)

    def test_missing_graph_package_path_or_matching_consumer_bytes_never_grants_proof(self):
        graph, packages = self.capture()
        tree = parse(SOURCE)
        variants = (
            dict(packages=packages, source_path='main.typ'),
            dict(packages=(), import_graph=graph, source_path='main.typ'),
            dict(packages=packages, import_graph=graph),
            dict(packages=packages, import_graph=graph, source_path='other.typ'),
            dict(packages=(replace(packages[0], source=packages[0].source + b'\n'),),
                 import_graph=graph, source_path='main.typ'),
        )
        for kwargs in variants:
            with self.subTest(kwargs=tuple(kwargs)):
                self.assertFalse(markers(tree, 'main.typ', resolve(tree, **kwargs), allow_followup_casing=True).edits)
        changed = parse(SOURCE + b'\n')
        stale = resolve(changed, packages=packages, import_graph=graph, source_path='main.typ')
        self.assertFalse(markers(changed, 'main.typ', stale, allow_followup_casing=True).edits)

    def test_fresh_candidate_context_rebases_bytes_but_never_adds_import_authority(self):
        contexts = {}
        report(self.root, 'check', package_path=self.package_root, _analysis_observer=contexts.__setitem__)
        context = contexts['main.typ']
        inserted = SOURCE.replace(b'this consequence', b'#paragraph_tab\nthis consequence')
        results = context.analyze(parse(inserted))
        self.assertTrue(results[CAPITALIZATION].edits)
        self.assertFalse(results[MARKER].edits)
        expanded = SOURCE.replace(SETUP, b'#import "styles.typ": count as new-name\n' + SETUP)
        self.assertFalse(context.analyze(parse(expanded))[MARKER].edits)
        stale = replace(context, tree=parse(SOURCE + b' '))
        self.assertFalse(stale.analyze(parse(inserted))[CAPITALIZATION].edits)

    def test_multihop_renamed_reexports_and_bounded_literals_preserve_genuine_api(self):
        self.put('leaf.typ', STYLES)
        self.styles.write_bytes(b'/* initializer trivia */\n#import "leaf.typ": saved-marker as prior, tabs\n'
                               b'#let saved-marker = prior\n#let count = 1\n#let saved-count = count\n'
                               b'#let yes = true\n#let absent = none\n#let automatic = auto\n#let title = "plain"\n')
        before, after, candidate, contexts = self.pair()
        self.assertEqual(candidate, SOURCE.replace(b'this consequence', b'#paragraph_tab\nThis consequence'))
        self.assertEqual({s.path for s in contexts['main.typ'].import_graph.sources},
                         {'main.typ', 'styles.typ', 'leaf.typ'})
        self.assert_verified(SOURCE, before, after, candidate, contexts)

    def test_cycle_and_depth_refusals_do_not_recover_from_package_names(self):
        self.put('leaf.typ', b'#import "styles.typ": saved-marker, tabs\n')
        self.styles.write_bytes(b'#import "leaf.typ": saved-marker, tabs\n')
        result = self.raw_marker()
        self.assertFalse(result.edits)
        self.styles.write_bytes(b'#import "leaf.typ": saved-marker, tabs\n')
        self.put('leaf.typ', STYLES)
        with patch.object(import_graph, 'MAX_GRAPH_DEPTH', 2):
            self.assertFalse(self.raw_marker().edits)

    def test_missing_then_created_dependency_stays_missing_in_captured_context(self):
        self.styles.unlink()
        contexts = {}
        original = import_graph.load_import_graph
        def created_after_capture(*args, **kwargs):
            captured = original(*args, **kwargs)
            self.styles.write_bytes(STYLES)
            return captured
        with patch.object(import_graph, 'load_import_graph', side_effect=created_after_capture):
            first = report(self.root, 'dry-run', package_path=self.package_root,
                           _analysis_observer=contexts.__setitem__)
        self.assertFalse(first['proposed_edits'])
        self.assertFalse(contexts['main.typ'].analyze(parse(SOURCE))[MARKER].edits)
        second = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertIn(MARKER, owners(second))

    def test_dependency_mutation_during_retained_capture_rejects_whole_snapshot(self):
        original = package_loader._Snapshot._read_bytes
        raced = False
        def mutate(fd, limit, path):
            nonlocal raced
            raw = original(fd, limit, path)
            if path == self.styles and not raced:
                raced = True
                self.styles.write_bytes(STYLES + b'// changed during capture\n')
            return raw
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=mutate):
            with self.assertRaises(ImportGraphError):
                report(self.root, 'check', package_path=self.package_root)
        self.assertTrue(raced)

    def test_core_captures_relative_graph_without_exposing_pde_rules(self):
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        contexts = {}
        value = report(self.root, 'dry-run', package_path=self.package_root,
                       _analysis_observer=contexts.__setitem__)
        self.assertIsNotNone(contexts['main.typ'].import_graph)
        self.assertEqual([p['import_path'] for p in value['package_evidence']], [TEXT_UTILS])
        self.assertEqual(value['coverage']['available_backends'], list(rule_ids('core')))
        self.assertFalse({MARKER, CAPITALIZATION, ORDER, REDUNDANT} & set(value['coverage']['available_backends']))
        self.assertFalse(value['proposed_edits'])
        self.target.write_bytes(DIRECT + SETUP + BODY)
        with patch.object(import_graph, 'load_import_graph', wraps=load_import_graph) as capture:
            report(self.root, 'check', package_path=self.package_root)
        self.assertEqual(capture.call_count, 0)

    def test_core_graph_capture_still_fails_closed_for_unsafe_paths_and_budgets(self):
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        with patch.object(import_graph, 'MAX_GRAPH_FILES', 1):
            with self.assertRaisesRegex(ImportGraphError, 'file limit'):
                report(self.root, 'check', package_path=self.package_root)
        outside = self.base / 'outside.typ'
        outside.write_bytes(STYLES)
        self.styles.unlink()
        self.styles.symlink_to(outside)
        with self.assertRaisesRegex(ImportGraphError, 'symbolic link'):
            report(self.root, 'check', package_path=self.package_root)
        self.assertEqual(outside.read_bytes(), STYLES)

    def test_all_selected_parsed_sources_seed_one_coherent_graph(self):
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ", "styles.typ"]\n')
        contexts = {}
        report(self.root, 'check', package_path=self.package_root, _analysis_observer=contexts.__setitem__)
        graph = contexts['main.typ'].import_graph
        self.assertEqual(graph.selected, ('main.typ', 'styles.typ'))
        self.assertIs(contexts['styles.typ'].import_graph, graph)
        self.assertEqual(graph.source_for('styles.typ').source, STYLES)

    def test_markdown_examples_never_capture_or_resolve_their_relative_imports(self):
        self.config.write_bytes(b'rule-set="core"\ninclude=["notes.md"]\n')
        self.put('notes.md', b'```typst\n#import "missing.typ": *\n$x^i$\n```\n')
        with patch.object(import_graph, 'load_import_graph', side_effect=AssertionError('Markdown graph read')), \
                patch.object(package_loader, 'load_packages', side_effect=AssertionError('Markdown package read')):
            value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertEqual(owners(value), {SCRIPT})
        self.assertFalse(value['package_evidence'])

    def test_dependency_parser_budget_refusal_is_preserved_during_package_request_gathering(self):
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        source = b'#import "bad.typ": unknown\n$x^i$\n'
        self.target.write_bytes(source)
        # Actual pinned adapter exceeds MAX_NODES. This is a real bounded
        # parser refusal, not a fabricated tree with errors/complete flags.
        self.put('bad.typ', b'#let a = (' + b'0,' * 50_000 + b')')
        for package_path in (None, self.package_root):
            with self.subTest(package_path=package_path):
                contexts = {}
                value = report(self.root, 'dry-run', package_path=package_path,
                               _analysis_observer=contexts.__setitem__)
                self.assertEqual(owners(value), {SCRIPT})
                self.assertEqual(proposed(source, value), source.replace(b'^i', b'^(i)'))
                refused = contexts['main.typ'].import_graph.unavailable
                self.assertTrue(any(u.path == 'bad.typ' and not u.request for u in refused))

    def test_facade_access_is_not_import_reordering_or_redundancy_authority(self):
        self.put('Styles/styles.typ', STYLES)
        self.put('Styles/local_tags.typ', b'#import "styles.typ": saved-marker, tabs\n')
        self.put('figures.typ', b'#let diagram = 2\n')
        source = (b'#import "figures.typ": diagram\n'
                  b'#import "Styles/styles.typ": saved-marker as paragraph_tab, tabs as apply-paragraph-tabs\n'
                  b'#import "Styles/local_tags.typ": saved-marker as retained\n' + SETUP + BODY)
        graph, packages = self.capture(source)
        self.assertFalse(GraphAnalyzer(graph).module('Styles/styles.typ').pure)
        results = analyze(parse(source), 'main.typ', pack='pde', packages=packages, import_graph=graph)
        self.assertFalse(results[ORDER].edits or results[REDUNDANT].edits)
        self.assertTrue(results[MARKER].edits)

    def test_other_reviewed_package_initializer_cannot_be_smuggled_through_unused_exports(self):
        self.copy_package('math-blocks', '0.2.0')
        self.styles.write_bytes(STYLES + b'#import "@local/math-blocks:0.2.0": dots_space as unused\n')
        value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertEqual({e['import_path'] for e in value['package_evidence']},
                         {TEXT_UTILS, '@local/math-blocks:0.2.0'})
        self.assertNotIn(MARKER, owners(value))

    def test_loader_metadata_and_exact_source_failures_remain_authoritative(self):
        manifest = self.package_root / 'local/text-utils/0.1.2/typst.toml'
        lib = self.package_root / 'local/text-utils/0.1.2/lib.typ'
        original_manifest, original_lib = manifest.read_bytes(), lib.read_bytes()
        for old, new in ((b'name = "text-utils"', b'name = "different"'),
                         (b'version = "0.1.2"', b'version = "0.1.3"'),
                         (b'entrypoint = "lib.typ"', b'entrypoint = "other.typ"')):
            with self.subTest(metadata=new):
                manifest.write_bytes(original_manifest.replace(old, new))
                value = report(self.root, 'dry-run', package_path=self.package_root)
                self.assertEqual(value['package_unavailable'][0]['code'], 'unreviewed-package-metadata')
                self.assertNotIn(MARKER, owners(value))
        manifest.write_bytes(original_manifest)
        lib.write_bytes(original_lib + b'\n')
        value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertEqual(value['package_unavailable'][0]['code'], 'unreviewed-package-source')
        self.assertNotIn(MARKER, owners(value))

    def test_authenticated_candidate_replay_after_source_deletion_never_rereads_dependencies(self):
        before, after, candidate, contexts = self.pair()
        self.assertNotEqual(candidate, SOURCE)
        for target in (self.target, self.styles, self.config,
                       *(self.package_root / relative for relative in self.package_bytes)):
            target.unlink()
        record, = before['convergence_evidence']
        original_read = Path.read_bytes
        def no_fixture_reads(path):
            if path.is_relative_to(self.base):
                self.fail('Candidate reopened captured consumer/dependency/package bytes')
            return original_read(path)  # Existing rule catalog is not a captured input.
        with patch.object(Path, 'read_bytes', no_fixture_reads), \
                patch.object(package_loader._Snapshot, '_read_bytes', side_effect=AssertionError('candidate descriptor reread')), \
                patch.object(package_loader, 'load_packages', side_effect=AssertionError('candidate package reload')), \
                patch.object(import_graph, 'load_import_graph', side_effect=AssertionError('candidate graph reload')):
            replay = replay_evidence(record, SOURCE, candidate, context=contexts['main.typ'])
            self.assertEqual(replay.content, candidate)
            self.assert_verified(SOURCE, before, after, candidate, contexts)
            self.assertFalse(contexts['main.typ'].analyze(parse(candidate))[MARKER].edits)

    def test_missing_stale_context_and_stripped_history_cannot_authenticate_facade_candidate(self):
        before, after, candidate, contexts = self.pair()
        self.assertNotEqual(candidate, SOURCE)
        stripped = copy.deepcopy(before)
        stripped.pop('convergence_evidence')
        for edit in stripped['proposed_edits']:
            edit.pop('repair_passes', None)
            edit.pop('rule_ids', None)
        for inputs in ({}, {'main.typ': replace(contexts['main.typ'], tree=parse(SOURCE + b' '))}):
            with self.subTest(stale=bool(inputs)):
                verdict = candidate_verdict(stripped, after, {'main.typ': SOURCE.decode()},
                                            {'main.typ': candidate.decode()}, analysis_contexts=inputs)
                self.assertFalse(verdict['verified'])
                self.assertEqual(verdict['status'], 'BLOCKED_ANALYSIS')

    def test_bom_crlf_unicode_and_later_ignore_ownership_survive_facade_multipass(self):
        source = (b'\xef\xbb\xbf' + (IMPORT + SETUP).replace(b'\n', b'\r\n')
                  + '이전 ends.#footnote[이유.]\r\n\r\n'.encode()
                  + b' \tthis consequence follows.\r\n\r\n'
                  + b'// typst-lint: ignore-next core.parenthesized-scripts -- preserve notation\r\n'
                  + b'$ z^j $\r\n')
        before, after, candidate, contexts = self.pair(source)
        expected = source.replace('이유.]\r\n'.encode(), '이유.]\r\n#parbreak()\r\n'.encode()).replace(
            b' \tthis consequence', b'#paragraph_tab\r\n \tThis consequence')
        self.assertEqual(candidate, expected)
        self.assertEqual(before['suppressed_by_rule'], {SCRIPT: 1})
        self.assertEqual(before['suppressed_by_rule'], after['suppressed_by_rule'])
        self.assert_verified(source, before, after, candidate, contexts)

    def test_config_ignore_does_not_reenable_facade_marker_or_its_followup_case(self):
        self.config.write_bytes(b'rule-set="pde"\ninclude=["main.typ"]\n[[ignore]]\n'
                                b'paths=["main.typ"]\nrules=["pde.prose-paragraph-marker"]\nreason="chosen"\n')
        value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertFalse({MARKER, CAPITALIZATION} & owners(value))
        self.assertEqual(proposed(SOURCE, value), SOURCE)
        self.assertEqual(value['suppressed_by_rule'].get(MARKER), 1)

    def test_real_cli_all_modes_are_read_only_with_compiler_tripwire_and_package_bytes_unchanged(self):
        output = self.put('previous.pdf', b'previous output')
        tripwire = self.put('bin/typst', b'#!/bin/sh\nprintf called > "$FACADE_COMPILER_MARKER"\nexit 97\n')
        tripwire.chmod(0o700)
        marker = self.base / 'compiler-called'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TYPST_PACKAGE_CACHE_PATH=str(self.cache),
                   PATH=str(tripwire.parent) + os.pathsep + os.environ.get('PATH', ''),
                   FACADE_COMPILER_MARKER=str(marker))
        expected = SOURCE.replace(b'this consequence', b'#paragraph_tab\nThis consequence')
        config = self.config.read_bytes()
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            with self.subTest(mode=mode):
                launcher = TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')
                command = [sys.executable, str(launcher), 'fix' if mode == 'dry-run' else mode,
                           '--root', str(self.root), '--package-path', str(self.package_root), '--format', 'json']
                if mode == 'dry-run':
                    command.append('--dry-run')
                if mode == 'compile':
                    command.extend(['--', 'main.typ', 'previous.pdf'])
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(proposed(SOURCE, value), expected)
                self.assertEqual(owners(value), {MARKER, CAPITALIZATION})
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
                self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
                self.assertEqual(self.target.read_bytes(), SOURCE)
                self.assertEqual(self.styles.read_bytes(), STYLES)
                self.assertEqual(self.config.read_bytes(), config)
                self.assertEqual(output.read_bytes(), b'previous output')
                self.assertFalse(marker.exists() or list(self.cache.rglob('*')))
                for relative, raw in self.package_bytes.items():
                    self.assertEqual((self.package_root / relative).read_bytes(), raw)
                    self.assertEqual((REPO / relative).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
