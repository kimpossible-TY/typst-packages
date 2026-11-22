"""Real test-owned source snapshots, not fabricated module purity attestations."""
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import import_graph as graph
import package_loader
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ImportGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-import-graph-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def capture(self, source, path='main.typ'):
        self.put(path, source)
        return graph.load_import_graph(self.root, ((path, source),))

    def test_relative_normalization_reexports_aliases_and_frozen_bytes(self):
        self.put('leaf.typ', b'#let original = 1\n#let another = original\n')
        self.put('Styles/styles.typ', b'#import "../leaf.typ": original as tag\n#let local-tag = tag\n')
        (self.root / 'Chapters/sub').mkdir(parents=True)
        source = b'#import "sub/../../Styles/./styles.typ": *\n'
        context = self.capture(source, 'Chapters/main.typ')
        self.assertEqual(context.target_for('Chapters/main.typ', 'sub/../../Styles/./styles.typ'), 'Styles/styles.typ')
        evidence = graph.GraphAnalyzer(context).module('Styles/styles.typ')
        self.assertTrue(evidence.pure)
        exports = dict(evidence.exports)
        self.assertEqual(exports['tag'], exports['local-tag'])
        self.assertEqual(exports['tag'].path, 'leaf.typ')
        self.assertEqual(evidence.reexports, frozenset(('tag', 'local-tag')))
        self.assertEqual(context.selected, ('Chapters/main.typ',))
        self.assertEqual({item.path for item in context.sources}, {'Chapters/main.typ', 'Styles/styles.typ', 'leaf.typ'})
        self.put('leaf.typ', b'#let original = 99\n')
        self.assertEqual(graph.GraphAnalyzer(context).module('Styles/styles.typ'), evidence)
        with self.assertRaises(FrozenInstanceError):
            context.selected = ()
        with self.assertRaises(FrozenInstanceError):
            context.sources[0].source = b'changed'

    def test_same_path_different_spellings_have_one_source_identity(self):
        self.put('leaf.typ', b'#let x = 1\n')
        (self.root / 'nested').mkdir()
        context = self.capture(b'#import "./leaf.typ": x\n#import "nested/../leaf.typ": x as y\n')
        self.assertEqual([item.path for item in context.sources], ['leaf.typ', 'main.typ'])
        evidence = graph.GraphAnalyzer(context).module('main.typ')
        self.assertTrue(evidence.pure)
        self.assertEqual(dict(evidence.exports)['x'], dict(evidence.exports)['y'])

    def test_imports_are_followed_but_includes_and_ambient_siblings_are_not(self):
        self.put('dep.typ', b'#let x = 1\n')
        self.put('include.typ', b'\xff')
        (self.root / 'unused.typ').symlink_to('/not/a/real/file')
        context = self.capture(b'#include "include.typ"\n#import "dep.typ": x\n')
        self.assertEqual({s.path for s in context.sources}, {'main.typ', 'dep.typ'})
        self.assertFalse(graph.GraphAnalyzer(context).module('main.typ').pure)

    def test_dynamic_and_package_imports_are_not_followed(self):
        context = self.capture(b'#import ("dep" + ".typ"): *\n#import "@preview/mannot:0.4.0": *\n')
        self.assertEqual(len(context.sources), 1)
        self.assertEqual(len(context.unavailable), 2)
        self.assertFalse(graph.GraphAnalyzer(context).module('main.typ').pure)

    def test_root_escape_absolute_backslash_and_non_typst_targets_refuse(self):
        requests = ('../outside.typ', '/outside.typ', 'x\\y.typ', 'other.json')
        for request in requests:
            with self.subTest(request=request), self.assertRaises(graph.ImportGraphError):
                graph.normalize_relative('main.typ', request)
        context = self.capture(b'#import "../outside.typ": *\n')
        self.assertFalse(context.edges)
        self.assertIn('escapes', context.unavailable[0].reason)

    def test_cancelled_path_components_are_still_verified(self):
        self.put('leaf.typ', b'#let x = 1\n')
        context = self.capture(b'#import "missing/../leaf.typ": *\n')
        self.assertFalse(context.edges)
        self.assertIn('missing', context.unavailable[0].reason)
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'linked').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(graph.ImportGraphError, 'symbolic link'):
            self.capture(b'#import "linked/../leaf.typ": *\n')

    def test_symlink_file_directory_and_hardlink_refuse(self):
        self.put('real.typ', b'#let x = 1\n')
        (self.root / 'linked.typ').symlink_to(self.root / 'real.typ')
        with self.assertRaisesRegex(graph.ImportGraphError, 'symbolic link'):
            self.capture(b'#import "linked.typ": *\n')
        (self.root / 'dir').mkdir()
        self.put('dir/source.typ', b'#let x = 1\n')
        (self.root / 'linked-dir').symlink_to(self.root / 'dir', target_is_directory=True)
        with self.assertRaisesRegex(graph.ImportGraphError, 'symbolic link'):
            self.capture(b'#import "linked-dir/source.typ": *\n')
        os.link(self.root / 'real.typ', self.root / 'hard.typ')
        with self.assertRaisesRegex(graph.ImportGraphError, 'Hard-linked'):
            self.capture(b'#import "hard.typ": *\n')

    def test_missing_malformed_and_cycles_refuse_without_recursion(self):
        context = self.capture(b'#import "missing.typ": *\n')
        self.assertEqual(context.unavailable[0].reason, 'Explicitly imported source is missing')
        self.put('broken.typ', b'#let =')
        context = self.capture(b'#import "broken.typ": *\n')
        self.assertIn('Malformed', graph.GraphAnalyzer(context).module('main.typ').reason)
        self.put('a.typ', b'#import "b.typ": *\n')
        self.put('b.typ', b'#import "a.typ": *\n')
        context = self.capture(b'#import "a.typ": *\n')
        self.assertIn('Cyclic', graph.GraphAnalyzer(context).module('main.typ').reason)

    def test_depth_file_byte_and_edge_bounds_refuse(self):
        self.put('a.typ', b'#import "b.typ": *\n')
        self.put('b.typ', b'#import "c.typ": *\n')
        self.put('c.typ', b'#let c = 3\n')
        source = b'#import "a.typ": *\n'
        with patch.object(graph, 'MAX_GRAPH_DEPTH', 1):
            context = self.capture(source)
            self.assertTrue(any('depth limit' in x.reason for x in context.unavailable))
            self.assertFalse(graph.GraphAnalyzer(context).module('main.typ').pure)
        with patch.object(graph, 'MAX_GRAPH_FILES', 1), self.assertRaisesRegex(graph.ImportGraphError, 'file limit'):
            self.capture(source)
        with patch.object(graph, 'MAX_GRAPH_BYTES', len(source)), self.assertRaisesRegex(graph.ImportGraphError, 'aggregate'):
            self.capture(source)
        with patch.object(graph, 'MAX_GRAPH_EDGES', 1), self.assertRaisesRegex(graph.ImportGraphError, 'edge limit'):
            self.capture(source)
        self.put('large.typ', b' ' * 500)
        with patch.object(graph, 'MAX_INPUT', 128), self.assertRaisesRegex(graph.ImportGraphError, 'byte limit'):
            self.capture(b'#import "large.typ": *\n')

    def test_cached_dependency_proof_keeps_the_total_depth_bound(self):
        self.put('a.typ', b'#import "b.typ": *\n')
        self.put('b.typ', b'#import "c.typ": *\n')
        self.put('c.typ', b'#let value = 1\n')
        context = self.capture(b'#import "a.typ": *\n')
        analyzer = graph.GraphAnalyzer(context)
        with patch.object(graph, 'MAX_GRAPH_DEPTH', 3):
            self.assertTrue(analyzer.module('a.typ').pure)
            self.assertFalse(analyzer.module('main.typ').pure)
            self.assertIn('depth', analyzer.module('main.typ').reason)

    def test_dynamic_requests_also_count_toward_the_edge_bound(self):
        self.put('a.typ', b'#import ("missing" + ".typ"): *\n')
        source = b'#import "a.typ": *\n'
        with patch.object(graph, 'MAX_GRAPH_EDGES', 1), self.assertRaisesRegex(graph.ImportGraphError, 'edge limit'):
            self.capture(source)

    def test_seed_iterable_consumption_stops_at_the_file_bound(self):
        def seeds():
            yield ('a.typ', b'')
            yield ('b.typ', b'')
            self.fail('Must not consume an unbounded seed iterable')
        with patch.object(graph, 'MAX_GRAPH_FILES', 1), self.assertRaisesRegex(graph.ImportGraphError, 'file limit'):
            graph.load_import_graph(self.root, seeds())

    def test_seed_changes_and_cross_file_snapshot_races_refuse(self):
        source = b'#import "leaf.typ": *\n'
        self.put('main.typ', b'changed before graph capture')
        with self.assertRaisesRegex(graph.ImportGraphError, 'Selected source changed'):
            graph.load_import_graph(self.root, (('main.typ', source),))
        self.put('leaf.typ', b'#let x = 1\n')
        original = package_loader._Snapshot.read
        def raced(snapshot, directory, name, limit):
            value = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                self.put('main.typ', source.replace(b'leaf', b'else'))
            return value
        with patch.object(package_loader._Snapshot, 'read', raced), self.assertRaisesRegex(graph.ImportGraphError, 'changed'):
            self.capture(source)

    def test_dependency_disappearing_after_initial_stat_is_not_downgraded_to_missing(self):
        self.put('leaf.typ', b'#let x = 1\n')
        source = b'#import "leaf.typ": *\n'
        original = os.open
        def raced(name, flags, *args, **kwargs):
            if name == 'leaf.typ':
                (self.root / 'leaf.typ').unlink()
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=raced), self.assertRaisesRegex(graph.ImportGraphError, 'safely capture'):
            self.capture(source)

    def test_directory_disappearing_after_initial_stat_is_a_capture_failure(self):
        self.put('dir/leaf.typ', b'#let x = 1\n')
        source = b'#import "dir/leaf.typ": *\n'
        original = os.open
        def raced(name, flags, *args, **kwargs):
            if name == 'dir':
                (self.root / 'dir').rename(self.root / 'moved')
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=raced), self.assertRaisesRegex(graph.ImportGraphError, 'safely capture'):
            self.capture(source)

    def test_inode_replacement_with_identical_bytes_is_a_race(self):
        source = b'#import "leaf.typ": *\n'
        self.put('leaf.typ', b'#let x = 1\n')
        original = package_loader._Snapshot.read
        def raced(snapshot, directory, name, limit):
            value = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                replacement = self.put('replacement.typ', source)
                os.replace(replacement, self.root / 'main.typ')
            return value
        with patch.object(package_loader._Snapshot, 'read', raced), self.assertRaisesRegex(graph.ImportGraphError, 'changed'):
            self.capture(source)

    def test_graph_never_uses_unchecked_path_reads(self):
        source = b'#import "leaf.typ": *\n'
        self.put('leaf.typ', b'#let x = 1\n')
        self.put('main.typ', source)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Unchecked pathname read')):
            context = graph.load_import_graph(self.root, (('main.typ', source),))
        self.assertTrue(graph.GraphAnalyzer(context).module('leaf.typ').pure)

    def test_unsupported_module_grammar_does_not_create_effect_proofs(self):
        invalid = (b'#let x = counter("x")\n', b'#let x = 1\n#let x = 2\n', b'#let x = other\n',
                   b'#let f(x) = x\n', b'#let x = [content]\n', b'#set text(lang: "en")\n',
                   b'#show: identity\n', b'Ordinary output\n', b'#let x = 1\n#x = 2\n')
        for source in invalid:
            with self.subTest(source=source):
                self.put('leaf.typ', source)
                context = self.capture(b'#import "leaf.typ": *\n')
                self.assertFalse(graph.GraphAnalyzer(context).module('leaf.typ').pure)
        self.put('leaf.typ', b'#let x = 1\n')
        self.put('dup.typ', b'#import "leaf.typ": x\n#import "leaf.typ": x\n')
        context = self.capture(b'#import "dup.typ": *\n')
        self.assertIn('Duplicate', graph.GraphAnalyzer(context).module('dup.typ').reason)

    def test_module_namespace_import_and_dotted_items_remain_unproved(self):
        self.put('leaf.typ', b'#let x = 1\n')
        for source in (b'#import "leaf.typ" as leaf\n', b'#import "leaf.typ": x.y\n',
                       b'#import "leaf.typ": x as same, x as same\n'):
            context = self.capture(source)
            self.assertFalse(graph.GraphAnalyzer(context).module('main.typ').pure)

    def test_rebase_is_exact_baseline_bound_and_cannot_add_import_requests(self):
        self.put('leaf.typ', b'#let x = 1\n')
        source = b'#import "leaf.typ": *\n#import "./leaf.typ": *\n'
        context = self.capture(source)
        candidate = b'#import "leaf.typ": *\n'
        changed = graph.rebase_graph(context, 'main.typ', source, candidate)
        self.assertEqual(changed.source_for('main.typ').source, candidate)
        self.assertEqual(context.source_for('main.typ').source, source)
        self.assertIs(graph.rebase_graph(context, 'main.typ', b'wrong', candidate), context)
        self.assertIs(graph.rebase_graph(context, 'other.typ', source, candidate), context)
        self.assertIs(graph.rebase_graph(context, 'main.typ', source, b'#import "else.typ": *\n'), context)
        self.assertEqual(changed.source_for('leaf.typ'), context.source_for('leaf.typ'))

    def test_invalid_seed_paths_duplicates_and_mutable_sources_refuse(self):
        for seed in (('../main.typ', b''), ('/main.typ', b''), ('main.typ', bytearray(b''))):
            with self.subTest(seed=seed), self.assertRaises(graph.ImportGraphError):
                graph.load_import_graph(self.root, (seed,))
        with self.assertRaises(graph.ImportGraphError):
            graph.load_import_graph(self.root, (('main.typ', b''), ('main.typ', b'')))


if __name__ == '__main__':
    unittest.main()
