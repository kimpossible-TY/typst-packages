"""Actual CLI, configured-source snapshots and whole-candidate composition."""
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
import package_loader
import typst_lint
from book_provenance import EntrypointSnapshot
from book_rules import SINGLE, RESET, RULE_IDS
from edit_plan import Edit, plan
from source_analysis import analyze, rule_ids
from static_resolver import MATH_BOOK, MATH_BLOCKS, MATH_BOOK_SOURCE_SHA256, MATH_BLOCKS_SOURCE_SHA256
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, digest, report

IMPORT = (b'#import "@local/math-book:0.2.0": apply-math-book\n'
          b'#import "@local/math-blocks:0.2.0": apply-math-block-reset\n')
BOOK = b'#show: apply-math-book.with(title: "Notes")\n'
RESET_SHOW = b'#show: apply-math-block-reset\n'
SOURCE = IMPORT + BOOK + BOOK + RESET_SHOW


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class BookIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-book-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root, self.packages, self.cache = (self.base / p for p in ('consumer', 'packages', 'empty-cache'))
        self.root.mkdir()
        self.cache.mkdir()
        self.target = self.root / 'main.typ'
        self.target.write_bytes(SOURCE)
        self.config = self.root / '.typst-lint.toml'
        self.config.write_text('rule-set="core"\nentrypoint="main.typ"\n')
        self.originals = {}
        # Only the two reviewed definition snapshots are supplied. In particular,
        # cover.typ and imported implementation dependencies are absent: linting
        # cannot evaluate either package as a side effect.
        for name in ('math-book', 'math-blocks'):
            for filename in ('lib.typ', 'typst.toml'):
                relative = Path('local') / name / '0.2.0' / filename
                raw = (REPO / relative).read_bytes()
                destination = self.packages / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
                self.originals[relative] = raw

    def test_real_cli_in_all_modes_reports_only_partial_diagnostics_without_writes_or_compilation(self):
        output = self.root / 'existing.pdf'
        output.write_bytes(b'OUTPUT SENTINEL')
        tripwire = self.base / 'bin'
        tripwire.mkdir()
        marker = self.base / 'compiler-called'
        compiler = tripwire / 'typst'
        compiler.write_text('#!/bin/sh\nprintf called > "$TYPST_TEST_COMPILER_MARKER"\nexit 97\n')
        compiler.chmod(0o700)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TYPST_PACKAGE_CACHE_PATH=str(self.cache),
                   PATH=str(tripwire) + os.pathsep + os.environ.get('PATH', ''),
                   TYPST_TEST_COMPILER_MARKER=str(marker))
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            with self.subTest(mode=mode):
                command = [sys.executable, str(TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')),
                           'fix' if mode == 'dry-run' else mode, '--root', str(self.root),
                           '--package-path', str(self.packages), '--format', 'json']
                if mode == 'dry-run':
                    command.append('--dry-run')
                if mode == 'compile':
                    command.extend(['--', 'main.typ', 'existing.pdf'])
                process = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertEqual({d['rule_id'] for d in value['diagnostics']}, set(RULE_IDS))
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['proposed_edits'])
                self.assertFalse(value['coverage']['complete'])
                self.assertEqual(value['hashes']['entrypoint'], digest(SOURCE))
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('core')))
                evidence = {r['import_path']: r['source_sha256'] for r in value['package_evidence']}
                self.assertEqual(evidence, {MATH_BOOK: MATH_BOOK_SOURCE_SHA256, MATH_BLOCKS: MATH_BLOCKS_SOURCE_SHA256})
                self.assertFalse(marker.exists() or list(self.cache.rglob('*')))
                self.assertEqual(self.target.read_bytes(), SOURCE)
                self.assertEqual(output.read_bytes(), b'OUTPUT SENTINEL')
        for relative, raw in self.originals.items():
            self.assertEqual((self.packages / relative).read_bytes(), raw)
            self.assertEqual((REPO / relative).read_bytes(), raw)

    def test_no_entrypoint_wrong_entrypoint_and_unselected_entrypoint_do_not_group_files(self):
        other = self.root / 'other.typ'
        other.write_bytes(b'An unrelated document.\n')
        chapter = self.root / 'chapter.typ'
        chapter.write_bytes(SOURCE)
        for settings in ('rule-set="core"\n',
                         'rule-set="core"\nentrypoint="other.typ"\n',
                         'rule-set="core"\nentrypoint="main.typ"\ninclude=["other.typ","chapter.typ"]\n',
                         'rule-set="core"\nentrypoint="main.typ"\nexclude=["main.typ"]\n'):
            self.config.write_text(settings)
            value = report(self.root, 'check', package_path=self.packages)
            self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.config.write_text('rule-set="core"\nentrypoint="main.typ"\n')
        self.target.write_bytes(IMPORT + BOOK + b'#include "chapter.typ"\n')
        value = report(self.root, 'check', package_path=self.packages)
        self.assertFalse(value['diagnostics'])
        self.assertTrue(any('include graphs' in reason for row in value['coverage']['files']
                            if row['rule_id'] in RULE_IDS for reason in row['reasons']))

    def test_ignored_and_suppressed_book_findings_follow_existing_configuration(self):
        self.config.write_text('rule-set="pde"\nentrypoint="main.typ"\n[[ignore]]\n'
            'paths=["main.typ"]\nrules=["core.single-book-show","core.no-duplicate-book-reset"]\nreason="chosen"\n')
        value = report(self.root, 'check', package_path=self.packages)
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['suppressed_count'], 2)
        self.config.write_text('rule-set="pde"\nentrypoint="main.typ"\n')
        self.target.write_bytes(IMPORT + BOOK
            + b'// typst-lint: ignore-next core.single-book-show -- example\n' + BOOK
            + b'// typst-lint: ignore-next core.no-duplicate-book-reset -- example\n' + RESET_SHOW)
        value = report(self.root, 'check', package_path=self.packages)
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['suppressed_by_rule'], {RESET: 1, SINGLE: 1})

    def test_paired_dependency_content_not_only_version_or_path_is_required(self):
        blocks = self.packages / 'local/math-blocks/0.2.0/lib.typ'
        blocks.write_bytes(blocks.read_bytes() + b'\n')
        value = report(self.root, 'check', package_path=self.packages)
        self.assertFalse(value['diagnostics'])
        self.assertEqual(value['package_unavailable'][0]['code'], 'unreviewed-package-source')
        self.target.write_bytes(IMPORT.splitlines(keepends=True)[0] + BOOK + BOOK)
        value = report(self.root, 'check', package_path=self.packages)
        self.assertFalse(value['diagnostics'])
        self.assertEqual([r['import_path'] for r in value['package_evidence']], [MATH_BOOK])

    def test_entrypoint_change_between_collection_and_snapshot_is_rejected(self):
        original = typst_lint.collect
        def changed(root, config):
            records = original(root, config)
            self.target.write_bytes(SOURCE + b'Changed.\n')
            return records
        with patch.object(typst_lint, 'collect', side_effect=changed):
            with self.assertRaisesRegex(InputError, 'entrypoint changed after input selection'):
                report(self.root, 'check', package_path=self.packages)

    def test_entrypoint_change_after_snapshot_cannot_supply_mismatched_tree_proofs(self):
        original = package_loader.read_snapshot_file
        def changed(path, limit):
            snapshot = original(path, limit)
            self.target.write_bytes(SOURCE + b'Changed.\n')
            return snapshot
        with patch.object(package_loader, 'read_snapshot_file', side_effect=changed):
            value = report(self.root, 'check', package_path=self.packages)
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['hashes']['entrypoint'], digest(SOURCE))
        self.assertTrue(any('Input changed during analysis' in reason for row in value['coverage']['files']
                            for reason in row['reasons']))

    def test_entrypoint_retained_descriptor_detects_in_place_mutation(self):
        original = package_loader._Snapshot._read_bytes
        changed = False
        def raced(fd, limit, path):
            nonlocal changed
            raw = original(fd, limit, path)
            if path == self.target and not changed:
                changed = True
                self.target.write_bytes(SOURCE.replace(b'Notes', b'Other'))
            return raw
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=raced):
            with self.assertRaisesRegex(InputError, 'Cannot safely read configured entrypoint'):
                report(self.root, 'check', package_path=self.packages)
        self.assertTrue(changed)

    def test_configured_parse_reread_rejects_identical_byte_outside_root_symlink(self):
        outside = self.base / 'outside.typ'
        outside.write_bytes(SOURCE)
        original_check, original_read = typst_lint.checked_path, Path.read_bytes
        checks, swapped = 0, False

        def raced(root, name):
            nonlocal checks, swapped
            result = original_check(root, name)
            if name == 'main.typ':
                checks += 1
                if checks == 3:  # collect, initial snapshot, selected parse reread
                    self.target.unlink()
                    self.target.symlink_to(outside)
                    swapped = True
            return result

        def no_unsafe_path_read(path):
            if path == self.target and swapped:
                self.fail('The configured parse reread must not use Path.read_bytes after its path check')
            return original_read(path)

        with patch.object(typst_lint, 'checked_path', side_effect=raced), \
                patch.object(Path, 'read_bytes', no_unsafe_path_read):
            value = report(self.root, 'check', package_path=self.packages)
        self.assertTrue(swapped and self.target.is_symlink())
        self.assertFalse(value['diagnostics'] or value['proposed_edits'] or value['compiler_invoked'])
        self.assertTrue(any('Cannot safely reread configured entrypoint' in reason
                            and 'symbolic link' in reason for row in value['coverage']['files']
                            for reason in row['reasons']))
        self.assertEqual(outside.read_bytes(), SOURCE)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Requires a POSIX FIFO')
    def test_configured_parse_reread_rejects_swapped_fifo_without_open_or_blocking(self):
        original_check, original_read, original_open = typst_lint.checked_path, Path.read_bytes, os.open
        checks, swapped = 0, False

        def raced(root, name):
            nonlocal checks, swapped
            result = original_check(root, name)
            if name == 'main.typ':
                checks += 1
                if checks == 3:
                    self.target.unlink()
                    os.mkfifo(self.target)
                    swapped = True
            return result

        def no_unsafe_path_read(path):
            if path == self.target and swapped:
                self.fail('A raced FIFO must not be passed to a blocking Path.read_bytes')
            return original_read(path)

        def no_fifo_open(path, flags, *args, **kwargs):
            if swapped and (path == self.target or path == 'main.typ'):
                self.fail('The retained-descriptor reader must reject the FIFO before opening it')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(typst_lint, 'checked_path', side_effect=raced), \
                patch.object(Path, 'read_bytes', no_unsafe_path_read), \
                patch.object(os, 'open', side_effect=no_fifo_open):
            value = report(self.root, 'check', package_path=self.packages)
        self.assertTrue(swapped)
        self.assertFalse(value['diagnostics'] or value['proposed_edits'] or value['compiler_invoked'])
        self.assertTrue(any('Cannot safely reread configured entrypoint' in reason
                            and 'not a regular file' in reason for row in value['coverage']['files']
                            for reason in row['reasons']))

    def test_whole_candidate_rebases_only_the_matching_immutable_entrypoint(self):
        source = b'Inline $x^i$.\n' + SOURCE
        tree = parse(source)
        packages = package_loader.load_packages(self.packages, (MATH_BOOK, MATH_BLOCKS)).sources
        context = EntrypointSnapshot('main.typ', source)
        results = analyze(tree, 'main.typ', pack='pde', packages=packages, entrypoint=context)
        self.assertEqual(set(results), set(rule_ids('pde')))
        self.assertEqual(len(results[SINGLE].diagnostics), 1)
        self.assertEqual(len(results[RESET].diagnostics), 1)
        proposals = tuple(e for result in results.values() for e in result.edits)
        self.assertEqual(len(proposals), 2)  # The script rule inserts each parenthesis separately.
        candidate = plan(source, proposals).candidate()
        self.assertEqual(candidate, source.replace(b'x^i', b'x^(i)'))
        final = analyze(parse(candidate), 'main.typ', pack='pde', packages=packages,
                        entrypoint=replace(context, source=candidate))
        self.assertFalse(any(r.edits for r in final.values()))
        self.assertEqual(len(final[SINGLE].diagnostics), 1)
        self.assertEqual(len(final[RESET].diagnostics), 1)
        self.assertFalse(any('no combined edits' in s for r in results.values() for s in r.skips))
        stale = analyze(tree, 'main.typ', packages=packages, entrypoint=replace(context, source=SOURCE))
        self.assertFalse(stale[SINGLE].diagnostics or stale[RESET].diagnostics)
        self.assertEqual(sum(len(r.edits) for r in stale.values()), 2)
        self.assertTrue(any('snapshot does not match' in s for s in stale[SINGLE].skips))


if __name__ == '__main__':
    unittest.main()
