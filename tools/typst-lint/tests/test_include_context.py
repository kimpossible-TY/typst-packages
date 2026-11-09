"""Read-only include evidence from real, test-owned files and the pinned AST.

These tests establish source/occurrence capture, never Typst evaluation, lexical
scope, rendering, compiler-resource identity, or permission to edit a source.
"""
import copy
from dataclasses import FrozenInstanceError, fields, replace
import gc
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import include_context as capture
import package_loader
from syntax_client import AdapterError, EXECUTABLE, Tree


def sha(source):
    return hashlib.sha256(source).hexdigest()


def tree_sha(tree):
    records = tuple((node.kind, node.start, node.end, node.children, node.errors,
                     node.operands, node.display) for node in tree.nodes)
    payload = ('include-tree-v1', 'typst-syntax-0.15.1', 'adapter-0.2.0-schema-2',
               tree.mode, records)
    return sha(json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('ascii'))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class IncludeContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-include-context-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def load(self, source, path='main.typ'):
        self.put(path, source)
        return capture.load_include_context(self.root, ((path, source),))

    def paths(self, context):
        return tuple(item.identity.path for item in context.sources)

    def occurrences(self, context, path):
        return tuple(item for item in context.occurrences if item.source.path == path)

    def edge_for(self, context, path, ordinal=0):
        return tuple(edge for edge in context.edges if edge.node.source.path == path)[ordinal]

    def assert_edge_identity(self, context, edge):
        """Reconstruct ancestors independently, without capture helper functions."""
        item = context.source_for(edge.node.source.path)
        self.assertIsInstance(item.tree, Tree)
        tree = item.tree
        self.assertEqual(item.source, tree.source)
        self.assertEqual(item.identity.source_sha256, sha(item.source))
        self.assertEqual(item.identity.tree_sha256, tree_sha(tree))
        self.assertEqual(edge.node.source, item.identity)
        node = tree.nodes[edge.node.node_id]
        self.assertEqual((edge.node.kind, edge.node.start, edge.node.end),
                         (node.kind, node.start, node.end))
        self.assertEqual(node.kind, 'ModuleInclude')
        parents = {child: index for index, parent in enumerate(tree.nodes)
                   for child in parent.children}
        ancestry, index = [], edge.node.node_id
        while index in parents:
            index = parents[index]
            ancestry.append(index)
        ancestry.reverse()
        self.assertEqual(tuple(owner.node_id for owner in edge.ancestry), tuple(ancestry))
        for owner in edge.ancestry:
            expected = tree.nodes[owner.node_id]
            self.assertEqual((owner.source, owner.kind, owner.start, owner.end),
                             (item.identity, expected.kind, expected.start, expected.end))
        self.assertEqual(edge.content_ancestry,
                         tuple(owner for owner in edge.ancestry if owner.kind == 'ContentBlock'))
        self.assertEqual(edge.call_ancestry,
                         tuple(owner for owner in edge.ancestry if owner.kind == 'FuncCall'))

    def test_root_source_identity_tree_and_empty_chain_are_frozen(self):
        source = 'A source-owned α.\n'.encode()
        context = self.load(source)
        self.assertEqual(context.project_root, str(self.root))
        self.assertEqual(self.paths(context), ('main.typ',))
        item = context.source_for('main.typ')
        self.assertEqual(item.source, source)
        self.assertEqual(item.identity.path, 'main.typ')
        self.assertEqual(item.identity.source_sha256, sha(source))
        self.assertEqual(item.identity.tree_sha256, tree_sha(item.tree))
        info = (self.root / 'main.typ').stat()
        self.assertEqual(item.fingerprint, (info.st_dev, info.st_ino, info.st_mode,
                         info.st_nlink, info.st_uid, info.st_gid, info.st_size,
                         info.st_mtime_ns, info.st_ctime_ns))
        self.assertEqual(item.tree.source, source)
        self.assertEqual(item.tree.mode, 'markup')
        self.assertEqual(context.entrypoints, (item.identity,))
        self.assertEqual(len(context.occurrences), 1)
        root = context.occurrences[0]
        self.assertEqual((root.entrypoint, root.source, root.edge_chain),
                         (item.identity, item.identity, ()))
        self.assertFalse(root.unresolved_codes)
        self.assertFalse(context.edges)
        self.assertIsNone(context.source_for('absent.typ'))
        for record, attribute, value in ((context, 'sources', ()),
                                         (item, 'source', b'changed'),
                                         (item.identity, 'path', 'else.typ'),
                                         (item.tree, 'source', b'changed'),
                                         (root, 'edge_chain', ())):
            with self.subTest(record=type(record).__name__), self.assertRaises(FrozenInstanceError):
                setattr(record, attribute, value)
        self.put('main.typ', b'Changed later.\n')
        self.assertEqual(item.source, source)
        self.assertEqual(item.tree.source, source)

    def test_source_identity_changes_for_exact_byte_changes_not_path_alone(self):
        source = b'#include "leaf.typ"\n'
        self.put('leaf.typ', b'Leaf.\n')
        first = self.load(source)
        second = self.load(source.replace(b'\n', b'\r\n'))
        self.assertEqual(first.entrypoints[0].path, second.entrypoints[0].path)
        self.assertNotEqual(first.entrypoints[0].source_sha256, second.entrypoints[0].source_sha256)
        self.assertNotEqual(first.entrypoints[0].tree_sha256, second.entrypoints[0].tree_sha256)
        self.assertNotEqual(first.edges[0].node, second.edges[0].node)
        self.assertEqual(first.source_for('leaf.typ').identity,
                         second.source_for('leaf.typ').identity)

    def test_literal_include_preserves_request_raw_operand_span_and_ancestry(self):
        leaf = b'Leaf.\n'
        self.put('Parts with spaces/leaf.typ', leaf)
        source = 'α before.\n#include "Parts with spaces/leaf.typ"\n'.encode()
        context = self.load(source)
        self.assertEqual(self.paths(context), ('Parts with spaces/leaf.typ', 'main.typ'))
        self.assertEqual(len(context.edges), 1)
        edge = context.edges[0]
        self.assert_edge_identity(context, edge)
        self.assertIsNotNone(edge.literal)
        self.assertEqual(edge.literal.kind, 'Str')
        self.assertEqual(edge.literal.source, edge.node.source)
        self.assertEqual(source[edge.literal.start:edge.literal.end], edge.raw_request)
        self.assertEqual(edge.request, 'Parts with spaces/leaf.typ')
        self.assertEqual(edge.raw_request, b'"Parts with spaces/leaf.typ"')
        self.assertEqual(edge.target, context.source_for('Parts with spaces/leaf.typ').identity)
        self.assertEqual(source[edge.node.start:edge.node.end],
                         b'include "Parts with spaces/leaf.typ"')
        self.assertFalse(edge.unresolved_codes)
        child = self.occurrences(context, 'Parts with spaces/leaf.typ')[0]
        self.assertEqual(child.edge_chain, (edge.node,))
        self.assertEqual(child.entrypoint, context.entrypoints[0])

    def test_repeated_sites_keep_distinct_ordered_occurrences_but_one_snapshot(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n#include "./leaf.typ"\n'
        context = self.load(source)
        self.assertEqual(self.paths(context), ('leaf.typ', 'main.typ'))
        self.assertEqual(len(context.edges), 2)
        a, b = context.edges
        self.assertNotEqual(a.node, b.node)
        self.assertEqual(a.target, b.target)
        self.assertLess(a.node.start, b.node.start)
        self.assertEqual(tuple(x.edge_chain for x in self.occurrences(context, 'leaf.typ')),
                         ((a.node,), (b.node,)))
        for edge in context.edges:
            self.assert_edge_identity(context, edge)

    def test_nested_relative_normalization_verifies_real_components(self):
        self.put('Parts/leaf.typ', b'Leaf.\n')
        (self.root / 'Chapters/sub').mkdir(parents=True)
        source = b'#include "sub/../../Parts/./leaf.typ"\n'
        context = self.load(source, 'Chapters/main.typ')
        edge = context.edges[0]
        self.assertEqual(edge.request, 'sub/../../Parts/./leaf.typ')
        self.assertEqual(edge.target.path, 'Parts/leaf.typ')
        self.assertEqual(self.occurrences(context, 'Parts/leaf.typ')[0].edge_chain, (edge.node,))

    def test_diamond_and_repeated_parent_expand_each_edge_chain(self):
        self.put('leaf.typ', b'Leaf.\n')
        self.put('a.typ', b'#include "leaf.typ"\n')
        self.put('b.typ', b'#include "leaf.typ"\n')
        context = self.load(b'#include "a.typ"\n#include "b.typ"\n#include "a.typ"\n')
        main = tuple(edge for edge in context.edges if edge.node.source.path == 'main.typ')
        a, b = self.edge_for(context, 'a.typ'), self.edge_for(context, 'b.typ')
        leaves = self.occurrences(context, 'leaf.typ')
        self.assertEqual(tuple(item.edge_chain for item in leaves),
                         ((main[0].node, a.node), (main[1].node, b.node), (main[2].node, a.node)))
        self.assertEqual(tuple(item.source.path for item in context.occurrences),
                         ('main.typ', 'a.typ', 'leaf.typ', 'b.typ', 'leaf.typ', 'a.typ', 'leaf.typ'))
        self.assertEqual(len(context.sources), 4)
        self.assertEqual(len(context.edges), 5)

    def test_multiple_entrypoints_have_independent_sorted_occurrence_roots(self):
        leaf = b'Leaf.\n'
        self.put('leaf.typ', leaf)
        a = b'#include "leaf.typ"\n'
        b = b'#include "leaf.typ"\n#include "leaf.typ"\n'
        self.put('a.typ', a)
        self.put('b.typ', b)
        context = capture.load_include_context(self.root, (('b.typ', b), ('a.typ', a)))
        self.assertEqual(tuple(item.path for item in context.entrypoints), ('a.typ', 'b.typ'))
        self.assertEqual(tuple((item.entrypoint.path, item.source.path, len(item.edge_chain))
                               for item in context.occurrences),
                         (('a.typ', 'a.typ', 0), ('a.typ', 'leaf.typ', 1),
                          ('b.typ', 'b.typ', 0), ('b.typ', 'leaf.typ', 1), ('b.typ', 'leaf.typ', 1)))
        self.assertEqual(self.paths(context), ('a.typ', 'b.typ', 'leaf.typ'))
        self.assertEqual(tuple(edge.node.source.path for edge in context.edges),
                         ('a.typ', 'b.typ', 'b.typ'))

    def test_same_named_sites_in_distinct_directories_keep_source_bound_identity(self):
        source = b'#include "../leaf.typ"\n'
        self.put('leaf.typ', b'Leaf.\n')
        self.put('one/main.typ', source)
        self.put('two/main.typ', source)
        context = capture.load_include_context(self.root,
            (('two/main.typ', source), ('one/main.typ', source)))
        one, two = context.edges
        self.assertEqual(one.node.source.source_sha256, two.node.source.source_sha256)
        self.assertEqual(one.node.source.tree_sha256, two.node.source.tree_sha256)
        self.assertEqual((one.node.node_id, one.node.start, one.node.end),
                         (two.node.node_id, two.node.start, two.node.end))
        self.assertNotEqual(one.node, two.node)
        self.assertEqual(one.target, two.target)
        leaves = self.occurrences(context, 'leaf.typ')
        self.assertEqual(tuple(item.edge_chain for item in leaves), ((one.node,), (two.node,)))
        self.assertEqual(tuple(item.entrypoint.path for item in leaves),
                         ('one/main.typ', 'two/main.typ'))

    def test_direct_and_indirect_cycles_keep_closing_occurrence_then_stop(self):
        for sources, expected_paths in (
                ({'main.typ': b'#include "main.typ"\n'}, ('main.typ', 'main.typ')),
                ({'main.typ': b'#include "a.typ"\n', 'a.typ': b'#include "main.typ"\n'},
                 ('main.typ', 'a.typ', 'main.typ'))):
            with self.subTest(sources=sources):
                for path, source in sources.items():
                    self.put(path, source)
                context = capture.load_include_context(self.root, (('main.typ', sources['main.typ']),))
                self.assertEqual(tuple(item.source.path for item in context.occurrences), expected_paths)
                closing = context.occurrences[-1]
                self.assertEqual(len(closing.edge_chain), len(expected_paths) - 1)
                self.assertIn('include-cycle', closing.unresolved_codes)
                self.assertEqual(closing.entrypoint, context.entrypoints[0])

    def test_comment_raw_and_string_lookalikes_are_not_include_sites(self):
        source = (b'// #include "line.typ"\n/* #include "block.typ" */\n'
                  b'`#include "raw.typ"`\n#let text = "#include \\\"string.typ\\\""\n')
        context = self.load(source)
        self.assertEqual(self.paths(context), ('main.typ',))
        self.assertFalse(context.edges)
        self.assertEqual(len(context.occurrences), 1)

    def test_utf8_bom_and_newline_forms_retain_exact_spans(self):
        self.put('leaf.typ', '한글 leaf.\n'.encode())
        for prefix, newline in ((b'', b'\n'), (b'', b'\r\n'), (b'\xef\xbb\xbf', b'\n')):
            source = prefix + 'αβ. '.encode() + newline + b'#include "leaf.typ"' + newline
            with self.subTest(prefix=prefix, newline=newline):
                context = self.load(source)
                item = context.source_for('main.typ')
                edge = self.edge_for(context, 'main.typ')
                self.assertEqual(item.source, source)
                self.assertEqual(item.identity.source_sha256, sha(source))
                self.assertEqual(source[edge.node.start:edge.node.end], b'include "leaf.typ"')
                self.assertEqual(edge.node.start, source.index(b'include'))
                self.assert_edge_identity(context, edge)

    def test_capture_does_not_follow_imports_or_discover_unused_bad_siblings(self):
        self.put('leaf.typ', b'Leaf.\n')
        self.put('import-only.typ', b'\xff')
        (self.root / 'unused.typ').symlink_to('/not/a/real/file')
        source = b'#let local = 1\n#import "import-only.typ": *\n#include "leaf.typ"\n'
        context = self.load(source)
        self.assertEqual(self.paths(context), ('leaf.typ', 'main.typ'))
        self.assertEqual(len(context.edges), 1)
        self.assertEqual(context.edges[0].target.path, 'leaf.typ')

    def test_no_parent_lexical_names_or_styles_are_merged_into_child_snapshot(self):
        child = b'#local\n'
        self.put('leaf.typ', child)
        source = b'#let local = 1\n#set text(size: 20pt)\n#include "leaf.typ"\n'
        context = self.load(source)
        item = context.source_for('leaf.typ')
        self.assertEqual(item.source, child)
        self.assertEqual(item.tree.source, child)
        self.assertEqual(item.identity.source_sha256, sha(child))
        for record in (context, item, context.edges[0], context.occurrences[-1]):
            names = {field.name for field in fields(record)}
            self.assertFalse(names.intersection(('exports', 'bindings', 'styles', 'environment',
                                                 'writable', 'write_authority', 'compile_authority',
                                                 'rendered', 'evaluated', 'initializer_proof')))

    def test_original_snapshots_survive_later_changes_without_live_currentness_claim(self):
        leaf = b'Leaf original.\n'
        self.put('leaf.typ', leaf)
        context = self.load(b'#include "leaf.typ"\n')
        identity = context.source_for('leaf.typ').identity
        self.put('leaf.typ', b'Leaf changed.\n')
        self.assertEqual(context.source_for('leaf.typ').source, leaf)
        self.assertEqual(context.source_for('leaf.typ').identity, identity)
        self.assertEqual(context.occurrences[-1].source, identity)

    def test_conditional_and_loop_literals_are_captured_but_explicitly_unresolved(self):
        self.put('leaf.typ', b'Leaf.\n')
        for source in (b'#if false [#include "leaf.typ"]\n',
                       b'#for value in () [#include "leaf.typ"]\n',
                       b'#while false [#include "leaf.typ"]\n'):
            with self.subTest(source=source):
                context = self.load(source)
                edge = context.edges[0]
                self.assertEqual(edge.target.path, 'leaf.typ')
                self.assertIn('conditional-containment', edge.unresolved_codes)
                self.assertIn('conditional-containment', context.occurrences[-1].unresolved_codes)
                self.assert_edge_identity(context, edge)
                self.assertTrue(any(refusal.node == edge.node and
                                    'conditional-containment' in refusal.codes
                                    for refusal in context.unresolved))

    def test_closure_context_and_show_literals_are_captured_without_execution_claim(self):
        self.put('leaf.typ', b'Leaf.\n')
        for source in (b'#let later() = [#include "leaf.typ"]\n',
                       b'#context [#include "leaf.typ"]\n',
                       b'#show heading: node => [#include "leaf.typ"]\n'):
            with self.subTest(source=source):
                context = self.load(source)
                edge = context.edges[0]
                self.assertEqual(edge.target.path, 'leaf.typ')
                self.assertIn('deferred-containment', edge.unresolved_codes)
                self.assertIn('deferred-containment', context.occurrences[-1].unresolved_codes)
                self.assert_edge_identity(context, edge)

    def test_stored_content_call_and_callback_keep_full_unknown_ancestry(self):
        self.put('leaf.typ', b'Leaf.\n')
        for source in (b'#let stored = [#include "leaf.typ"]\n',
                       b'#unknown([#include "leaf.typ"])\n',
                       b'#unknown(() => [#include "leaf.typ"])\n'):
            with self.subTest(source=source):
                context = self.load(source)
                edge = context.edges[0]
                self.assertEqual(edge.target.path, 'leaf.typ')
                expected_code = 'deferred-containment' if b'=>' in source else 'unknown-containment'
                self.assertIn(expected_code, edge.unresolved_codes)
                self.assertTrue(edge.content_ancestry)
                if source.startswith(b'#unknown'):
                    self.assertTrue(edge.call_ancestry)
                if b'=>' in source:
                    self.assertIn('deferred-containment', edge.unresolved_codes)
                    self.assertNotIn('unknown-containment', edge.unresolved_codes)
                self.assert_edge_identity(context, edge)

    def test_unresolved_ancestor_codes_propagate_through_descendant_occurrences(self):
        self.put('leaf.typ', b'Leaf.\n')
        self.put('parent.typ', b'#include "leaf.typ"\n')
        context = self.load(b'#if false [#include "parent.typ"]\n')
        leaf = self.occurrences(context, 'leaf.typ')[0]
        self.assertIn('conditional-containment', leaf.unresolved_codes)
        self.assertEqual(len(leaf.edge_chain), 2)
        self.assertFalse(self.edge_for(context, 'parent.typ').unresolved_codes)

    def test_nested_conditional_and_deferred_containment_preserve_both_meanings(self):
        self.put('leaf.typ', b'Leaf.\n')
        context = self.load(b'#let later() = [#if false [#include "leaf.typ"]]\n')
        edge = context.edges[0]
        self.assertEqual(edge.unresolved_codes,
                         ('conditional-containment', 'deferred-containment'))
        self.assertEqual(context.occurrences[-1].unresolved_codes, edge.unresolved_codes)
        self.assert_edge_identity(context, edge)

    def test_dynamic_escaped_package_absolute_invalid_and_missing_requests(self):
        self.put('leaf.typ', b'Leaf.\n')
        cases = (
            (b'#include ("leaf" + ".typ")\n', 'dynamic-request', None, None),
            (b'#include "le\\u{61}f.typ"\n', 'escaped-request', None, 'Str'),
            (b'#include "@preview/example:0.1.0"\n', 'package-request',
             '@preview/example:0.1.0', 'Str'),
            (b'#include "/outside.typ"\n', 'absolute-request', '/outside.typ', 'Str'),
            (b'#include ""\n', 'invalid-request', '', 'Str'),
            (b'#include "../outside.typ"\n', 'root-escape', '../outside.typ', 'Str'),
            (b'#include "other.txt"\n', 'non-typst-request', 'other.txt', 'Str'),
            (b'#include "missing.typ"\n', 'missing-source', 'missing.typ', 'Str'),
            (b'#include "missing/leaf.typ"\n', 'missing-source', 'missing/leaf.typ', 'Str'),
        )
        for source, code, request, literal_kind in cases:
            with self.subTest(code=code, request=request):
                context = self.load(source)
                self.assertEqual(self.paths(context), ('main.typ',))
                self.assertEqual(len(context.edges), 1)
                edge = context.edges[0]
                self.assertIsNone(edge.target)
                self.assertEqual(edge.request, request)
                self.assertEqual(None if edge.literal is None else edge.literal.kind, literal_kind)
                self.assertIn(code, edge.unresolved_codes)
                self.assertEqual(len(context.occurrences), 1)
                self.assertEqual(len(context.unresolved), 1)
                refusal = context.unresolved[0]
                self.assertEqual((refusal.node, refusal.codes, refusal.entrypoint, refusal.edge_chain),
                                 (edge.node, edge.unresolved_codes, None, ()))
                self.assert_edge_identity(context, edge)

    def test_cycle_unresolved_record_is_occurrence_bound_not_a_global_edge_failure(self):
        self.put('a.typ', b'#include "main.typ"\n')
        context = self.load(b'#include "a.typ"\n')
        closing = context.occurrences[-1]
        refusal = tuple(item for item in context.unresolved if item.entrypoint is not None)
        self.assertEqual(len(refusal), 1)
        self.assertEqual((refusal[0].entrypoint, refusal[0].edge_chain),
                         (closing.entrypoint, closing.edge_chain))
        self.assertEqual(refusal[0].node, closing.edge_chain[-1])
        self.assertIn('include-cycle', refusal[0].codes)
        self.assertTrue(all('include-cycle' not in edge.unresolved_codes for edge in context.edges))

    def test_malformed_root_and_dependency_fail_as_syntax_errors(self):
        for path in ('main.typ', 'leaf.typ'):
            with self.subTest(path=path), self.assertRaises(capture.IncludeSyntaxError):
                if path == 'main.typ':
                    self.load(b'#let =\n')
                else:
                    self.put('leaf.typ', b'#let =\n')
                    self.load(b'#include "leaf.typ"\n')

    def test_adapter_failure_is_a_typed_failure_not_empty_success(self):
        source = b'Ordinary source.\n'
        self.put('main.typ', source)
        with patch.object(capture, 'parse', side_effect=AdapterError('test unavailable')) as parser, \
             self.assertRaises(capture.IncludeSyntaxError):
            capture.load_include_context(self.root, (('main.typ', source),))
        parser.assert_called_once_with(source)

    def test_invalid_utf8_seed_and_dependency_are_typed_errors(self):
        with self.assertRaises(capture.IncludeContextError):
            self.load(b'\xff')
        self.put('leaf.typ', b'\xff')
        with self.assertRaises(capture.IncludeContextError):
            self.load(b'#include "leaf.typ"\n')

    def test_seed_paths_pairs_duplicates_and_mutable_bytes_are_rejected(self):
        invalid = (('../main.typ', b''), ('/main.typ', b''), ('./main.typ', b''),
                   ('nested/../main.typ', b''), ('a//main.typ', b''), ('main.txt', b''),
                   ('main.typ', bytearray()), ('main.typ', memoryview(b'')),
                   ('main.typ', ''), (b'main.typ', b''), ('\ud800.typ', b''),
                   ('nul\x00.typ', b''), ('line\n.typ', b''))
        for seed in invalid:
            with self.subTest(seed=repr(seed)), self.assertRaises(capture.IncludeContextError):
                capture.load_include_context(self.root, (seed,))
        for seeds in (None, (), 'main.typ', b'main.typ', (None,), (('main.typ',),),
                      (['main.typ', b''],),
                      (('main.typ', b'', 'extra'),),
                      (('main.typ', b''), ('main.typ', b''))):
            with self.subTest(seeds=repr(seeds)), self.assertRaises(capture.IncludeContextError):
                capture.load_include_context(self.root, seeds)

    def test_invalid_or_noncanonical_explicit_roots_are_input_errors(self):
        for root in (None, b'/tmp', '', '.', 'relative-root',
                     str(self.root) + '/.', str(self.root) + '/../other',
                     str(self.root) + '\x00', '\ud800'):
            with self.subTest(root=repr(root)), self.assertRaises(capture.IncludeInputError):
                capture.load_include_context(root, (('main.typ', b''),))

    def test_oversized_entrypoint_bytes_fail_before_any_filesystem_read_or_parser(self):
        source = b'Root exact.\n'
        with patch.object(capture, 'MAX_SOURCE_BYTES', len(source)):
            self.assertEqual(self.load(source).source_for('main.typ').source, source)
        with patch.object(capture, 'MAX_SOURCE_BYTES', len(source) - 1), \
             patch.object(os, 'open', side_effect=AssertionError('Oversized seed must fail before open')), \
             patch.object(capture, 'parse', side_effect=AssertionError('Oversized seed must fail before parse')), \
             self.assertRaises(capture.IncludeLimitError):
            capture.load_include_context(self.root, (('main.typ', source),))

    def test_seed_bytes_are_verified_again_before_parser_receives_them(self):
        self.put('main.typ', b'Disk differs.\n')
        with patch.object(capture, 'parse', side_effect=AssertionError('Must verify seed first')), \
             self.assertRaises(capture.IncludeCaptureError):
            capture.load_include_context(self.root, (('main.typ', b'Captured earlier.\n'),))

    def test_file_aggregate_and_per_source_bounds_accept_exact_boundary(self):
        leaf = b'L' * 128
        source = b'#include "leaf.typ"\n'
        self.put('leaf.typ', leaf)
        for constant, boundary in (('MAX_CONTEXT_FILES', 2),
                                   ('MAX_CONTEXT_BYTES', len(source) + len(leaf)),
                                   ('MAX_SOURCE_BYTES', len(leaf))):
            with self.subTest(constant=constant):
                with patch.object(capture, constant, boundary):
                    self.assertEqual(len(self.load(source).sources), 2)
                with patch.object(capture, constant, boundary - 1), \
                     self.assertRaises(capture.IncludeLimitError):
                    self.load(source)

    def test_unknown_or_nonliteral_budget_refuses_before_capture_side_effects(self):
        class BudgetName(str):
            pass
        for value in ('book', 'BOOK-V1', '', None, True, 64, {}, [], BudgetName('book-v1')):
            with self.subTest(value=value), \
                 patch.object(capture, '_absolute_root', side_effect=AssertionError('Unexpected root read')), \
                 patch.object(capture, 'parse', side_effect=AssertionError('Unexpected parser')), \
                 self.assertRaises(capture.IncludeInputError):
                capture.load_include_context(self.root, (('main.typ', b'Root.\n'),), budget=value)

    def test_book_budget_explicitly_extends_file_capture_with_a_fixed_ceiling(self):
        for index in range(64):
            self.put(f'part-{index}.typ', b'Literal chapter.\n')
        source = b''.join(f'#include "part-{index}.typ"\n'.encode() for index in range(63))
        self.put('main.typ', source)
        for kwargs in ({}, {'budget': 'standard-v1'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(capture.IncludeLimitError):
                capture.load_include_context(self.root, (('main.typ', source),), **kwargs)
        context = capture.load_include_context(self.root, (('main.typ', source),), budget='book-v1')
        self.assertEqual(len(context.sources), 64)
        self.assertEqual(len(context.occurrences), 64)
        self.assertEqual(context.budget, 'book-v1')
        self.assertIs(capture.assert_include_context(context), context)
        overflow = source + b'#include "part-63.typ"\n'
        self.put('main.typ', overflow)
        with self.assertRaises(capture.IncludeLimitError):
            capture.load_include_context(self.root, (('main.typ', overflow),), budget='book-v1')

    def test_book_budget_captures_dense_book_without_widening_scoped_analysis(self):
        # Five independently captured 44,001-node chapters exceed the old
        # aggregate limit while each obeys the unchanged adapter limit.
        chapter = b'$ x_i $\n' * 4000
        source = b''.join(f'#include "dense-{index}.typ"\n'.encode() for index in range(5))
        for index in range(5):
            self.put(f'dense-{index}.typ', chapter)
        self.put('main.typ', source)
        with self.assertRaisesRegex(capture.IncludeLimitError, 'aggregate syntax node'):
            capture.load_include_context(self.root, (('main.typ', source),))
        context = capture.load_include_context(self.root, (('main.typ', source),), budget='book-v1')
        nodes = sum(len(row.tree.nodes) for row in context.sources)
        self.assertGreater(nodes, 200_000)
        self.assertLess(nodes, 400_000)
        self.assertEqual(len(context.edges), 5)
        self.assertIs(capture.assert_include_context(context), context)
        # Capture headroom is not permission for a downstream analyzer to
        # silently exceed its independently documented work limits.
        import scoped_paragraph_context
        with self.assertRaisesRegex(scoped_paragraph_context.EvidenceError, 'aggregate byte/node'):
            scoped_paragraph_context.analyze_scoped_paragraph_context(context)
        with patch.object(capture, 'BOOK_MAX_CONTEXT_NODES', nodes - 1), \
             self.assertRaisesRegex(capture.IncludeLimitError, 'aggregate syntax node'):
            capture.load_include_context(self.root, (('main.typ', source),), budget='book-v1')

    def test_book_budget_retains_shared_byte_and_include_count_ceilings(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n#include "leaf.typ"\n'
        self.put('main.typ', source)
        for constant, boundary in (('MAX_CONTEXT_BYTES', len(source) + len(b'Leaf.\n')),
                                   ('MAX_INCLUDE_NODES', 2)):
            with self.subTest(constant=constant):
                with patch.object(capture, constant, boundary):
                    context = capture.load_include_context(self.root, (('main.typ', source),), budget='book-v1')
                    self.assertEqual(len(context.occurrences), 3)
                with patch.object(capture, constant, boundary - 1), \
                     self.assertRaises(capture.IncludeLimitError):
                    capture.load_include_context(self.root, (('main.typ', source),), budget='book-v1')

    def test_selected_budget_is_bound_to_original_issuance(self):
        self.put('main.typ', b'Root.\n')
        for selected, replacement in (('standard-v1', 'book-v1'), ('book-v1', 'standard-v1')):
            context = capture.load_include_context(self.root, (('main.typ', b'Root.\n'),), budget=selected)
            object.__setattr__(context, 'budget', replacement)
            with self.subTest(selected=selected), self.assertRaises(capture.IncludeCaptureError):
                capture.assert_include_context(context)
            object.__setattr__(context, 'budget', selected)
            self.assertIs(capture.assert_include_context(context), context)

    def test_include_node_bound_counts_unsupported_requests_too(self):
        source = b'#include "missing.typ"\n#include ("dynamic" + ".typ")\n'
        with patch.object(capture, 'MAX_INCLUDE_NODES', 2):
            self.assertEqual(len(self.load(source).edges), 2)
        with patch.object(capture, 'MAX_INCLUDE_NODES', 1), self.assertRaises(capture.IncludeLimitError):
            self.load(source)

    def test_depth_bound_accepts_exact_edge_chain_and_rejects_next_level(self):
        self.put('a.typ', b'#include "b.typ"\n')
        self.put('b.typ', b'Leaf.\n')
        source = b'#include "a.typ"\n'
        with patch.object(capture, 'MAX_INCLUDE_DEPTH', 2):
            self.assertEqual(len(self.load(source).occurrences[-1].edge_chain), 2)
        with patch.object(capture, 'MAX_INCLUDE_DEPTH', 1), self.assertRaises(capture.IncludeLimitError):
            self.load(source)

    def test_occurrence_bound_counts_roots_repeated_sites_and_cycle_closures(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n#include "leaf.typ"\n'
        with patch.object(capture, 'MAX_OCCURRENCES', 3):
            self.assertEqual(len(self.load(source).occurrences), 3)
        with patch.object(capture, 'MAX_OCCURRENCES', 2), self.assertRaises(capture.IncludeLimitError):
            self.load(source)
        with patch.object(capture, 'MAX_OCCURRENCES', 1), self.assertRaises(capture.IncludeLimitError):
            self.load(b'#include "main.typ"\n')

    def test_shared_source_bytes_and_ast_nodes_count_once_not_per_occurrence(self):
        leaf = b'Leaf.\n'
        self.put('leaf.typ', leaf)
        source = b'#include "leaf.typ"\n#include "leaf.typ"\n'
        context = self.load(source)
        node_count = sum(len(item.tree.nodes) for item in context.sources)
        with patch.object(capture, 'MAX_CONTEXT_BYTES', len(source) + len(leaf)), \
             patch.object(capture, 'MAX_CONTEXT_NODES', node_count):
            self.assertEqual(len(self.load(source).occurrences), 3)
        with patch.object(capture, 'MAX_CONTEXT_NODES', node_count - 1), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(source)

    def test_bounded_seed_iterator_stops_before_unbounded_consumption(self):
        def seeds():
            yield ('a.typ', b'')
            yield ('b.typ', b'')
            self.fail('Capture consumed past the bounded seed prefix')
        with patch.object(capture, 'MAX_CONTEXT_FILES', 1), self.assertRaises(capture.IncludeLimitError):
            capture.load_include_context(self.root, seeds())

    def test_path_byte_bound_accepts_exact_utf8_size_and_rejects_overflow(self):
        source = b'#include "parts/leaf.typ"\n'
        self.put('parts/leaf.typ', b'Leaf.\n')
        boundary = len('parts/leaf.typ'.encode())
        with patch.object(capture, 'MAX_PATH_BYTES', boundary):
            self.assertEqual(self.load(source).edges[0].target.path, 'parts/leaf.typ')
        with patch.object(capture, 'MAX_PATH_BYTES', boundary - 1), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(source)
        # The entrypoint denominator must use UTF-8 bytes, not character count.
        path = '한.typ'
        self.put(path, b'Root.\n')
        with patch.object(capture, 'MAX_PATH_BYTES', len(path.encode())):
            context = capture.load_include_context(self.root, ((path, b'Root.\n'),))
            self.assertEqual(context.entrypoints[0].path, path)
        with patch.object(capture, 'MAX_PATH_BYTES', len(path.encode()) - 1), \
             self.assertRaises(capture.IncludeLimitError):
            capture.load_include_context(self.root, ((path, b'Root.\n'),))
        path = '길/한글.typ'
        self.put(path, b'Leaf.\n')
        source = ('#include "' + path + '"\n').encode()
        with patch.object(capture, 'MAX_PATH_BYTES', len(path.encode())):
            self.assertEqual(self.load(source).edges[0].target.path, path)
        with patch.object(capture, 'MAX_PATH_BYTES', len(path.encode()) - 1), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(source)

    def test_normalized_target_path_also_obeys_path_byte_bound(self):
        self.put('long-dir/leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n'
        self.put('long-dir/main.typ', source)
        boundary = len('long-dir/main.typ'.encode())
        # The seed is longer than its target. A separate selected root keeps
        # the target path's own exact bound independently observable.
        with patch.object(capture, 'MAX_PATH_BYTES', boundary):
            self.assertEqual(self.load(source, 'long-dir/main.typ').edges[0].target.path,
                             'long-dir/leaf.typ')
        path = 'd/main.typ'
        request = 'longer.typ'
        self.put('d/longer.typ', b'Leaf.\n')
        self.put(path, ('#include "' + request + '"\n').encode())
        with patch.object(capture, 'MAX_PATH_BYTES', len('d/longer.typ') - 1), \
             self.assertRaises(capture.IncludeLimitError):
            capture.load_include_context(self.root,
                ((path, ('#include "' + request + '"\n').encode()),))

    def test_total_path_bytes_count_unique_canonical_sources_once(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n#include "./leaf.typ"\n'
        boundary = len(b'main.typ') + len(b'leaf.typ')
        with patch.object(capture, 'MAX_TOTAL_PATH_BYTES', boundary):
            self.assertEqual(len(self.load(source).sources), 2)
        with patch.object(capture, 'MAX_TOTAL_PATH_BYTES', boundary - 1), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(source)

    def test_directory_bound_counts_project_root_and_canceled_directories(self):
        self.put('one/two/leaf.typ', b'Leaf.\n')
        source = b'#include "one/two/leaf.typ"\n'
        with patch.object(capture, 'MAX_CONTEXT_DIRECTORIES', 3):
            self.assertEqual(self.load(source).edges[0].target.path, 'one/two/leaf.typ')
        with patch.object(capture, 'MAX_CONTEXT_DIRECTORIES', 2), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(source)
        self.put('leaf.typ', b'Leaf.\n')
        (self.root / 'cancel').mkdir()
        with patch.object(capture, 'MAX_CONTEXT_DIRECTORIES', 1), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(b'#include "cancel/../leaf.typ"\n')

    def test_root_byte_and_component_bounds_accept_exact_values(self):
        source = b'Root.\n'
        for constant, boundary in (('MAX_ROOT_BYTES', len(str(self.root).encode())),
                                   ('MAX_ROOT_COMPONENTS', len(self.root.parts))):
            with self.subTest(constant=constant):
                with patch.object(capture, constant, boundary):
                    self.assertEqual(len(self.load(source).sources), 1)
                with patch.object(capture, constant, boundary - 1), \
                     self.assertRaises(capture.IncludeLimitError):
                    self.load(source)

    def test_request_expression_and_total_request_bounds_include_dynamic_sites(self):
        source = b'#include ("dynamic" + ".typ")\n#include "missing.typ"\n'
        dynamic = b'("dynamic" + ".typ")'
        literal = b'"missing.typ"'
        for constant, boundary in (('MAX_REQUEST_EXPRESSION_BYTES', len(dynamic)),
                                   ('MAX_TOTAL_REQUEST_BYTES', len(dynamic) + len(literal))):
            with self.subTest(constant=constant):
                with patch.object(capture, constant, boundary):
                    context = self.load(source)
                    self.assertEqual(tuple(edge.raw_request for edge in context.edges), (dynamic, literal))
                with patch.object(capture, constant, boundary - 1), \
                     self.assertRaises(capture.IncludeLimitError):
                    self.load(source)

    def test_ancestry_budget_counts_every_include_node_not_unique_ancestors(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#unknown([\n#include "leaf.typ"\n#include "leaf.typ"\n])\n'
        initial = self.load(source)
        count = sum(len(edge.ancestry) for edge in initial.edges)
        self.assertGreater(count, len(initial.edges))
        with patch.object(capture, 'MAX_ANCESTRY_NODES', count):
            self.assertEqual(len(self.load(source).edges), 2)
        with patch.object(capture, 'MAX_ANCESTRY_NODES', count - 1), \
             self.assertRaises(capture.IncludeLimitError):
            self.load(source)

    def test_symlink_root_and_ancestor_are_rejected(self):
        source = b'Root source.\n'
        self.put('project/main.typ', source)
        (self.root / 'linked').symlink_to(self.root / 'project', target_is_directory=True)
        for root in (self.root / 'linked', self.root / 'linked/sub'):
            with self.subTest(root=root), self.assertRaises(capture.IncludeCaptureError):
                capture.load_include_context(root, (('main.typ', source),))

    def test_symlink_source_and_directory_are_rejected(self):
        self.put('real.typ', b'Leaf.\n')
        (self.root / 'linked.typ').symlink_to(self.root / 'real.typ')
        self.put('real/leaf.typ', b'Leaf.\n')
        (self.root / 'linked-dir').symlink_to(self.root / 'real', target_is_directory=True)
        for request in ('linked.typ', 'linked-dir/leaf.typ'):
            with self.subTest(request=request), self.assertRaises(capture.IncludeCaptureError):
                self.load(('#include "' + request + '"\n').encode())

    def test_seed_symlink_hardlink_and_directory_are_rejected(self):
        original = self.put('real.typ', b'Leaf.\n')
        (self.root / 'linked.typ').symlink_to(original)
        os.link(original, self.root / 'hard.typ')
        (self.root / 'directory.typ').mkdir()
        for path in ('linked.typ', 'hard.typ', 'directory.typ'):
            with self.subTest(path=path), self.assertRaises(capture.IncludeCaptureError):
                capture.load_include_context(self.root, ((path, b'Leaf.\n'),))

    def test_hardlinked_include_source_is_rejected(self):
        original = self.put('real.typ', b'Leaf.\n')
        os.link(original, self.root / 'hard.typ')
        with self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "hard.typ"\n')

    def test_case_aliases_are_rejected_when_filesystem_exposes_one_inode(self):
        target = self.put('CaseLeaf.typ', b'Leaf.\n')
        alternate = self.root / 'caseleaf.typ'
        if not alternate.exists() or not os.path.samefile(target, alternate):
            self.skipTest('Requires a case-insensitive test filesystem')
        with self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "CaseLeaf.typ"\n#include "caseleaf.typ"\n')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Requires POSIX FIFO')
    def test_fifo_is_rejected_before_open_and_never_blocks(self):
        os.mkfifo(self.root / 'fifo.typ')
        source = b'#include "fifo.typ"\n'
        self.put('main.typ', source)
        original = os.open
        def opening(name, flags, *args, **kwargs):
            if name == 'fifo.typ':
                self.fail('Visible FIFO must be rejected before open')
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), self.assertRaises(capture.IncludeCaptureError):
            capture.load_include_context(self.root, (('main.typ', source),))

    def test_canceled_missing_component_is_not_normalized_into_existing_target(self):
        self.put('leaf.typ', b'Leaf.\n')
        context = self.load(b'#include "missing/../leaf.typ"\n')
        self.assertEqual(self.paths(context), ('main.typ',))
        self.assertIsNone(context.edges[0].target)
        self.assertIn('missing-source', context.edges[0].unresolved_codes)

    def test_canceled_symlink_component_is_rejected_even_when_target_is_cached(self):
        self.put('leaf.typ', b'Leaf.\n')
        (self.root / 'real').mkdir()
        (self.root / 'linked').symlink_to(self.root / 'real', target_is_directory=True)
        for prefix in (b'', b'#include "leaf.typ"\n'):
            with self.subTest(cached=bool(prefix)), self.assertRaises(capture.IncludeCaptureError):
                self.load(prefix + b'#include "linked/../leaf.typ"\n')

    def test_cached_target_does_not_erase_missing_original_traversal(self):
        self.put('leaf.typ', b'Leaf.\n')
        context = self.load(b'#include "leaf.typ"\n#include "missing/../leaf.typ"\n')
        self.assertEqual(self.paths(context), ('leaf.typ', 'main.typ'))
        first, second = context.edges
        self.assertEqual(first.target.path, 'leaf.typ')
        self.assertIsNone(second.target)
        self.assertIn('missing-source', second.unresolved_codes)
        self.assertEqual(len(self.occurrences(context, 'leaf.typ')), 1)

    def test_popped_traversal_directory_remains_retained_until_final_verification(self):
        (self.root / 'sub').mkdir()
        self.put('leaf.typ', b'Leaf.\n')
        original = package_loader._Snapshot.read
        def reading(snapshot, directory, name, limit):
            source = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                (self.root / 'sub').rename(self.root / 'old-sub')
                (self.root / 'sub').mkdir()
            return source
        with patch.object(package_loader._Snapshot, 'read', reading), \
             self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "sub/../leaf.typ"\n')

    def test_dependency_disappearing_after_initial_presence_is_not_missing_success(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n'
        self.put('main.typ', source)
        original = os.open
        def opening(name, flags, *args, **kwargs):
            if name == 'leaf.typ':
                (self.root / 'leaf.typ').unlink()
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), self.assertRaises(capture.IncludeCaptureError):
            capture.load_include_context(self.root, (('main.typ', source),))

    def test_directory_disappearing_after_initial_presence_is_capture_failure(self):
        self.put('sub/leaf.typ', b'Leaf.\n')
        source = b'#include "sub/leaf.typ"\n'
        self.put('main.typ', source)
        original = os.open
        def opening(name, flags, *args, **kwargs):
            if name == 'sub':
                (self.root / 'sub').rename(self.root / 'old-sub')
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), self.assertRaises(capture.IncludeCaptureError):
            capture.load_include_context(self.root, (('main.typ', source),))

    def test_same_byte_inode_replacement_between_stat_and_open_is_rejected(self):
        leaf = b'Leaf.\n'
        self.put('leaf.typ', leaf)
        self.put('replacement.typ', leaf)
        source = b'#include "leaf.typ"\n'
        self.put('main.typ', source)
        original = os.open
        def opening(name, flags, *args, **kwargs):
            if name == 'leaf.typ':
                os.replace(self.root / 'replacement.typ', self.root / 'leaf.typ')
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), self.assertRaises(capture.IncludeCaptureError):
            capture.load_include_context(self.root, (('main.typ', source),))

    def test_same_byte_inode_replacement_during_read_is_rejected(self):
        leaf = b'Leaf.\n'
        self.put('leaf.typ', leaf)
        replacement = self.put('replacement.typ', leaf)
        original = package_loader._Snapshot._read_bytes
        def reading(fd, limit, path):
            source = original(fd, limit, path)
            if path.name == 'leaf.typ' and replacement.exists():
                os.replace(replacement, self.root / 'leaf.typ')
            return source
        with patch.object(package_loader._Snapshot, '_read_bytes', staticmethod(reading)), \
             self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "leaf.typ"\n')

    def test_final_verification_rejects_same_byte_seed_inode_replacement(self):
        source = b'#include "leaf.typ"\n'
        self.put('leaf.typ', b'Leaf.\n')
        original = package_loader._Snapshot.read
        def reading(snapshot, directory, name, limit):
            value = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                replacement = self.put('replacement.typ', source)
                os.replace(replacement, self.root / 'main.typ')
            return value
        with patch.object(package_loader._Snapshot, 'read', reading), \
             self.assertRaises(capture.IncludeCaptureError):
            self.load(source)

    def test_final_verification_rejects_same_size_in_place_edit(self):
        self.put('leaf.typ', b'Leaf.\n')
        original = package_loader._Snapshot.read
        def reading(snapshot, directory, name, limit):
            source = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                path = self.root / 'leaf.typ'
                before = path.stat()
                path.write_bytes(b'X' + source[1:])
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return source
        with patch.object(package_loader._Snapshot, 'read', reading), \
             self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "leaf.typ"\n')

    def test_final_byte_reread_is_independent_of_metadata_fingerprint(self):
        self.put('leaf.typ', b'Leaf.\n')
        original = package_loader._Snapshot.read
        def reading(snapshot, directory, name, limit):
            source = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                self.put('leaf.typ', b'X' + source[1:])
            return source
        # Real bytes and descriptors; only metadata comparison is neutralized
        # to establish the separate final content comparison's failure path.
        with patch.object(package_loader._Snapshot, 'read', reading), \
             patch.object(package_loader, '_file_signature', return_value=()), \
             self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "leaf.typ"\n')

    def test_final_verification_rejects_directory_replacement_with_same_files(self):
        leaf = b'Leaf.\n'
        self.put('sub/leaf.typ', leaf)
        original = package_loader._Snapshot.read
        def reading(snapshot, directory, name, limit):
            source = original(snapshot, directory, name, limit)
            if name == 'leaf.typ':
                (self.root / 'sub').rename(self.root / 'old-sub')
                self.put('sub/leaf.typ', leaf)
            return source
        with patch.object(package_loader._Snapshot, 'read', reading), \
             self.assertRaises(capture.IncludeCaptureError):
            self.load(b'#include "sub/leaf.typ"\n')

    def test_snapshot_uses_only_readonly_nofollow_nonblocking_descriptor_opens(self):
        leaf = b'Leaf.\n'
        source = b'#include "sub/leaf.typ"\n'
        self.put('sub/leaf.typ', leaf)
        self.put('main.typ', source)
        before = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                  for path in (self.root / 'main.typ', self.root / 'sub/leaf.typ')}
        original, opened = os.open, []
        def opening(name, flags, *args, **kwargs):
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertFalse(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            self.assertTrue(flags & os.O_NOFOLLOW)
            if str(name).endswith('.typ'):
                self.assertTrue(flags & os.O_NONBLOCK)
                self.assertIsNotNone(kwargs.get('dir_fd'))
            opened.append((name, flags))
            return original(name, flags, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opening), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('Unchecked pathname read')), \
             patch.object(Path, 'write_bytes', side_effect=AssertionError('Unexpected source write')), \
             patch.object(Path, 'write_text', side_effect=AssertionError('Unexpected source write')), \
             patch.object(os, 'scandir', side_effect=AssertionError('Unexpected directory crawl')):
            context = capture.load_include_context(self.root, (('main.typ', source),))
        self.assertEqual(self.paths(context), ('main.typ', 'sub/leaf.typ'))
        self.assertTrue(opened)
        for path, expected in before.items():
            self.assertEqual((path.read_bytes(), stat.S_IMODE(path.stat().st_mode)), expected)

    def test_retained_descriptors_close_on_success_and_capture_failure(self):
        self.put('leaf.typ', b'Leaf.\n')
        original = os.open
        for source in (b'#include "leaf.typ"\n', b'#let =\n'):
            opened = []
            self.put('main.typ', source)
            def opening(name, flags, *args, **kwargs):
                fd = original(name, flags, *args, **kwargs)
                opened.append(fd)
                return fd
            with self.subTest(source=source), patch.object(os, 'open', side_effect=opening):
                if source.startswith(b'#let'):
                    with self.assertRaises(capture.IncludeSyntaxError):
                        capture.load_include_context(self.root, (('main.typ', source),))
                else:
                    capture.load_include_context(self.root, (('main.typ', source),))
            self.assertTrue(opened)
            for fd in opened:
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_only_pinned_syntax_process_runs_no_evaluator_compiler_or_download(self):
        import socket
        import subprocess
        import urllib.request
        source = b'#include "leaf.typ"\n'
        self.put('main.typ', source)
        self.put('leaf.typ', b'#read("unread-file.txt")\n#unknown()\n')
        original, commands = subprocess.Popen, []
        def spawning(command, *args, **kwargs):
            self.assertEqual(command, [str(EXECUTABLE), 'markup'])
            commands.append(tuple(command))
            return original(command, *args, **kwargs)
        with patch.object(subprocess, 'Popen', side_effect=spawning), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('Unexpected network')), \
             patch.object(urllib.request, 'urlopen', side_effect=AssertionError('Unexpected download')), \
             patch.object(package_loader, 'load_packages', side_effect=AssertionError('Unexpected packages')):
            context = capture.load_include_context(self.root, (('main.typ', source),))
        self.assertEqual(self.paths(context), ('leaf.typ', 'main.typ'))
        self.assertEqual(len(commands), 2)

    def test_origin_assertion_is_pure_and_does_not_refresh_stale_disk_evidence(self):
        self.put('leaf.typ', b'Leaf.\n')
        context = self.load(b'#include "leaf.typ"\n')
        self.put('leaf.typ', b'Changed after capture.\n')
        with patch.object(capture, 'parse', side_effect=AssertionError('Unexpected reparse')), \
             patch.object(os, 'stat', side_effect=AssertionError('Unexpected live stat')), \
             patch.object(os, 'lstat', side_effect=AssertionError('Unexpected live lstat')), \
             patch.object(os, 'open', side_effect=AssertionError('Unexpected live open')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('Unexpected live read')):
            self.assertIs(capture.assert_include_context(context), context)
        self.assertEqual(context.source_for('leaf.typ').source, b'Leaf.\n')

    def test_constructor_copy_deepcopy_and_replace_do_not_obtain_parser_origin(self):
        context = self.load(b'Root.\n')
        constructed = capture.IncludeContext(**{field.name: getattr(context, field.name)
                                               for field in fields(context)})
        for other in (constructed, copy.copy(context), copy.deepcopy(context), replace(context)):
            with self.subTest(kind=type(other).__name__), self.assertRaises(capture.IncludeCaptureError):
                capture.assert_include_context(other)
        self.assertIs(capture.assert_include_context(context), context)

    def test_origin_registry_does_not_keep_context_alive_after_collection(self):
        context = self.load(b'Root.\n')
        key = id(context)
        reference = weakref.ref(context)
        self.assertIn(key, capture._ISSUED)
        del context
        gc.collect()
        self.assertIsNone(reference())
        self.assertNotIn(key, capture._ISSUED)

    def test_origin_assertion_rejects_deleted_fields_with_typed_failure(self):
        for location in ('context', 'source', 'tree', 'identity', 'node'):
            context = self.load(b'Root.\n')
            source = context.source_for('main.typ')
            record, name = {'context': (context, 'edges'), 'source': (source, 'source'),
                            'tree': (source.tree, 'nodes'), 'identity': (source.identity, 'path'),
                            'node': (source.tree.nodes[0], 'children')}[location]
            object.__delattr__(record, name)
            with self.subTest(location=location), self.assertRaises(capture.IncludeCaptureError):
                capture.assert_include_context(context)

    def test_origin_assertion_rejects_nested_changes_and_bool_integer_substitution(self):
        self.put('leaf.typ', b'Leaf.\n')
        source = b'#include "leaf.typ"\n'
        for location in ('source-hash', 'tree-span', 'tree-errors', 'edge-node-id',
                         'edge-request', 'occurrence-chain', 'fingerprint'):
            context = self.load(source)
            item = context.source_for('main.typ')
            edge = self.edge_for(context, 'main.typ')
            if location == 'source-hash':
                object.__setattr__(item.identity, 'source_sha256', '0' * 64)
            elif location == 'tree-span':
                object.__setattr__(item.tree.nodes[0], 'start', True)
            elif location == 'tree-errors':
                object.__setattr__(item.tree.nodes[0], 'errors', 0)
            elif location == 'edge-node-id':
                object.__setattr__(edge.node, 'node_id', True)
            elif location == 'edge-request':
                object.__setattr__(edge, 'request', 'else.typ')
            elif location == 'occurrence-chain':
                object.__setattr__(context.occurrences[-1], 'edge_chain', ())
            else:
                object.__setattr__(item, 'fingerprint', (True, *item.fingerprint[1:]))
            with self.subTest(location=location), self.assertRaises(capture.IncludeCaptureError):
                capture.assert_include_context(context)

    def test_origin_assertion_rejects_self_links_without_recursive_traversal(self):
        for location in ('context', 'tree-node', 'source-identity', 'occurrence'):
            context = self.load(b'Root.\n')
            item = context.source_for('main.typ')
            if location == 'context':
                object.__setattr__(context, 'sources', (context,))
            elif location == 'tree-node':
                object.__setattr__(item.tree.nodes[0], 'children', (item.tree.nodes[0],))
            elif location == 'source-identity':
                object.__setattr__(item.identity, 'path', item.identity)
            else:
                object.__setattr__(context.occurrences[0], 'edge_chain', (context.occurrences[0],))
            with self.subTest(location=location), self.assertRaises(capture.IncludeCaptureError):
                capture.assert_include_context(context)

    def test_origin_assertion_refuses_oversized_tuples_before_element_traversal(self):
        context = self.load(b'Root.\n')
        occurrence = context.occurrences[0]
        object.__setattr__(context, 'occurrences', (occurrence,) * (capture.MAX_OCCURRENCES + 1))
        with self.assertRaises(capture.IncludeCaptureError):
            capture.assert_include_context(context)


if __name__ == '__main__':
    unittest.main()
