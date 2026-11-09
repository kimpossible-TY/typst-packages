"""Real marker insertion followed by separately proved capitalization.

The opt-in marker backend proposes only insertion. Actual subsequent parser,
resolver and paragraph rules must produce the one-byte capitalization repair.
All source writes below initialize isolated test fixtures, never consumers.
"""
from dataclasses import FrozenInstanceError, replace
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
from capitalization import WORDS
from edit_plan import Edit, plan
from package_loader import load_packages
from paragraph_rules import CAPITALIZATION, analyze as paragraphs
from prose_marker_rules import RULE_ID, analyze as markers
from source_analysis import analyze
from static_resolver import ParagraphInsertionProof, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, resolve
from suppression import resolve as suppressions
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, load_rules, report

IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
SHOW = b'#show: apply-paragraph-tabs\n'
LANG = b'#set text(lang: "en")\n'
PREFIX = IMPORT + SHOW + LANG
BODY = b'First idea.\n\nthis consequence follows.\n'
KNOWN = {rule['id'] for rule in load_rules()[0]['rules']}


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class MarkerFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_packages((TOOL / 'tests/fixtures/reviewed-packages'), (TEXT_UTILS,))
        cls.packages = cls.loaded.sources
        assert len(cls.packages) == 1
        assert digest(cls.packages[0].source) == TEXT_UTILS_SOURCE_SHA256

    def direct(self, source, *, language=None, packages=None, allow=True):
        tree = parse(source)
        resolution = resolve(tree, packages=self.packages if packages is None else packages,
                             fallback_language=language)
        return tree, resolution, markers(tree, 'main.typ', resolution,
                                         allow_followup_casing=allow)

    def composed(self, source, *, language=None, ignored=frozenset()):
        return analyze(parse(source), 'main.typ', pack='pde', packages=self.packages,
                       fallback_language=language, ignored=ignored)

    def candidate(self, source, results):
        proposed = plan(source, (edit for result in results.values() for edit in result.edits))
        self.assertFalse(proposed.deferred)
        return proposed.candidate()

    def test_default_backend_remains_identical_and_opt_in_returns_only_insertion(self):
        self.assertEqual(len(WORDS), 26)
        for word in sorted(WORDS):
            with self.subTest(word=word):
                source = PREFIX + b'First idea.\n\n' + word + b' holds.\n'
                tree, resolution, opted = self.direct(source)
                default = markers(tree, 'main.typ', resolution)
                self.assertEqual(default, markers(tree, 'main.typ', resolution,
                                                  allow_followup_casing=False))
                self.assertFalse(default.edits)
                self.assertTrue(any('later capitalization repair' in s for s in default.skips))
                at = source.index(word + b' holds.')
                self.assertEqual(opted.edits, (Edit(at, at, b'#paragraph_tab\n'),))
                self.assertEqual({d.rule_id for d in opted.diagnostics}, {RULE_ID})
                self.assertFalse(paragraphs(tree, 'main.typ', resolution)[CAPITALIZATION].edits)

                # An authentic second parse/resolution produces the actual
                # casing proposal, never the marker preflight's fragment.
                inserted = plan(source, opted.edits).candidate()
                self.assertEqual(inserted, source[:at] + b'#paragraph_tab\n' + source[at:])
                next_tree = parse(inserted)
                next_resolution = resolve(next_tree, packages=self.packages)
                casing = paragraphs(next_tree, 'main.typ', next_resolution)[CAPITALIZATION]
                start = at + len(b'#paragraph_tab\n')
                self.assertEqual(casing.edits, (Edit(start, start + 1, word[:1].upper()),))
                self.assertEqual(len(casing.diagnostics), 1)
                self.assertFalse(markers(next_tree, 'main.typ', next_resolution,
                                         allow_followup_casing=True).edits)

    def test_all_26_actual_composed_repairs_have_real_two_pass_history(self):
        for word in sorted(WORDS):
            with self.subTest(word=word):
                source = PREFIX + b'First idea.\n\n' + word + b' holds.\n'
                results = self.composed(source)
                expected = source.replace(word + b' holds.', b'#paragraph_tab\n' + word.capitalize() + b' holds.')
                self.assertEqual(self.candidate(source, results), expected, results[RULE_ID].skips)
                self.assertFalse(results[CAPITALIZATION].diagnostics)
                self.assertEqual(results[RULE_ID].repair_passes, 2)
                first, second = results[RULE_ID].repair_steps
                self.assertEqual(first.before, source)
                self.assertEqual(first.after, second.before)
                self.assertEqual(second.after, expected)
                self.assertEqual({rid for e in first.applied for rid in e.rule_ids}, {RULE_ID})
                self.assertEqual({rid for e in second.applied for rid in e.rule_ids}, {CAPITALIZATION})
                self.assertTrue(all(e.edit.end == e.edit.start + 1 and len(e.edit.replacement) == 1
                                    for e in second.applied))
                self.assertEqual(plan(first.before, first.edits).candidate(), first.after)
                self.assertEqual(plan(second.before, second.edits).candidate(), second.after)
                causes = [c for result in results.values() for e in result.attributed_edits
                          for c in e.contributions]
                self.assertEqual({(c.pass_number, c.rule_id) for c in causes},
                                 {(1, RULE_ID), (2, CAPITALIZATION)})
                combined = [e for result in results.values() for e in result.attributed_edits]
                self.assertEqual(len(combined), 1)
                self.assertEqual(set(combined[0].rule_ids), {RULE_ID, CAPITALIZATION})
                self.assertEqual(self.composed(source), results)
                final = self.composed(expected)
                self.assertFalse(any(r.edits or r.diagnostics for r in final.values()))

    def test_multiple_insertions_reparse_once_then_all_real_casing_repairs_converge(self):
        source = PREFIX + b'First idea.\n\n' + b'\n\n'.join(word + b' holds.' for word in sorted(WORDS)) + b'\n'
        tree = parse(source)
        resolution = resolve(tree, packages=self.packages)
        with patch('prose_marker_rules.parse', wraps=parse) as parser:
            inserted = markers(tree, 'main.typ', resolution, allow_followup_casing=True)
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(len(inserted.edits), 26)
        results = self.composed(source)
        expected = PREFIX + b'First idea.\n\n' + b'\n\n'.join(
            b'#paragraph_tab\n' + word.capitalize() + b' holds.' for word in sorted(WORDS)) + b'\n'
        self.assertEqual(self.candidate(source, results), expected)
        first, second = results[RULE_ID].repair_steps
        self.assertEqual((len(first.applied), len(second.applied)), (26, 26))
        self.assertEqual(first.before, source)
        self.assertEqual(first.after, second.before)
        self.assertEqual(second.after, expected)
        self.assertFalse(any(r.edits or r.diagnostics for r in self.composed(expected).values()))

    def test_existing_uppercase_behavior_is_identical_with_or_without_opt_in(self):
        for word in sorted(WORDS):
            for first in (word.capitalize(), word.upper()):
                with self.subTest(first=first):
                    source = PREFIX + b'First idea.\n\n' + first + b' holds.\n'
                    tree, resolution, opted = self.direct(source)
                    self.assertEqual(opted, markers(tree, 'main.typ', resolution))

    def test_only_explicit_true_opts_into_a_followup_repair(self):
        source = PREFIX + BODY
        tree, resolution, _ = self.direct(source)
        for value in (False, None, 1, 'true', object()):
            with self.subTest(value=value):
                result = markers(tree, 'main.typ', resolution, allow_followup_casing=value)
                self.assertFalse(result.edits)

    def test_source_english_and_fallback_do_not_override_language_refusals(self):
        for lang, fallback, eligible in (
                (LANG, None, True), (b'#set text(lang: "en-US")\n', None, True),
                (b'', 'en', True), (b'', None, False),
                (b'#set text(lang: "ko")\n', 'en', False),
                (b'#set text(lang: "de")\n', 'en', False),
                (b'#set text(lang: variable)\n', 'en', False),
                (b'#set text(lang: "en") if condition\n', 'en', False)):
            with self.subTest(lang=lang, fallback=fallback):
                source = IMPORT + SHOW + lang + BODY
                result = self.direct(source, language=fallback)[2]
                self.assertEqual(bool(result.edits), eligible, result.skips)
                results = self.composed(source, language=fallback)
                expected = source.replace(b'this consequence', b'#paragraph_tab\nThis consequence') if eligible else source
                self.assertEqual(self.candidate(source, results), expected)
                if not eligible:
                    self.assertTrue(any('fix_unavailable:' in reason for reason in result.skips))

    def test_protected_names_unsupported_initials_and_incomplete_words_are_not_recased(self):
        for first in (b'iPhone remains.', b'eBay remains.', b'LaTeX remains.', b'tHis holds.',
                      b'Second idea.', b'x holds.', b'this, follows.', b'this-like holds.',
                      'Élan holds.'.encode(), '따라서 성립.'.encode(), b'this'):
            with self.subTest(first=first):
                source = PREFIX + b'First idea.\n\n' + first
                result = self.direct(source)[2]
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.edits)
                self.assertTrue(any('fix_unavailable:' in reason for reason in result.skips))
                self.assertEqual(self.candidate(source, self.composed(source)), source)

    def test_bom_crlf_indentation_gaps_and_unrelated_text_are_exact(self):
        for newline in (b'\n', b'\r\n'):
            source = (b'\xef\xbb\xbf' + PREFIX.replace(b'\n', newline)
                      + '이전 idea.'.encode() + newline + b' \t' + newline * 2
                      + b' \tthis consequence follows.' + newline)
            results = self.composed(source)
            self.assertEqual(self.candidate(source, results),
                             source.replace(b' \tthis', b'#paragraph_tab' + newline + b' \tThis'))
            self.assertEqual(results[RULE_ID].repair_passes, 2)

    def test_config_ignores_do_not_reenable_either_rule_or_lose_ownership(self):
        source = PREFIX + BODY
        for ignored, expected, owners in (
                ({RULE_ID}, source, set()),
                ({CAPITALIZATION}, source.replace(b'this', b'#paragraph_tab\nthis'), {RULE_ID}),
                ({RULE_ID, CAPITALIZATION}, source, set())):
            with self.subTest(ignored=ignored):
                results = self.composed(source, ignored=frozenset(ignored))
                self.assertEqual(self.candidate(source, results), expected)
                self.assertEqual({rid for result in results.values()
                                  for item in getattr(result, 'attributed_edits', ())
                                  for rid in item.rule_ids}, owners)
                self.assertTrue(all(not results[rid].edits for rid in ignored))

    def test_inline_ignores_keep_their_actual_syntax_targets(self):
        for rid in (RULE_ID, CAPITALIZATION, 'core.parenthesized-scripts'):
            source = PREFIX + b'First idea.\n\n// typst-lint: ignore-next ' + rid.encode() + b' -- preserve target\nthis holds.\n'
            tree, _, result = self.direct(source)
            scope, = suppressions(tree, KNOWN)
            self.assertEqual(source[scope.start:scope.end], b'this holds.')
            self.assertFalse(result.diagnostics or result.edits)
            self.assertEqual(self.candidate(source, self.composed(source)), source)

        source = PREFIX + BODY + (b'\n// typst-lint: ignore-next pde.paragraph-capitalization -- keep this casing\n'
                                  b'#paragraph_tab\nthis preserved paragraph.\n')
        results = self.composed(source)
        expected = source.replace(b'this consequence', b'#paragraph_tab\nThis consequence')
        self.assertEqual(self.candidate(source, results), expected)
        old_scope, = suppressions(parse(source), KNOWN)
        new_scope, = suppressions(parse(expected), KNOWN)
        self.assertEqual(source[old_scope.start:old_scope.end], expected[new_scope.start:new_scope.end])
        self.assertEqual((old_scope.rules, old_scope.reason), (new_scope.rules, new_scope.reason))
        self.assertEqual(len(results[CAPITALIZATION].suppressed), 1)
        self.assertEqual(len(self.composed(expected)[CAPITALIZATION].suppressed), 1)

    def test_opt_in_does_not_grant_new_binding_or_paragraph_ownership(self):
        for prefix, packages in (
                (IMPORT + LANG, self.packages), (IMPORT + LANG + BODY + SHOW, self.packages),
                (PREFIX, ()),
                (PREFIX, (replace(self.packages[0], source=self.packages[0].source + b'\n'),)),
                (IMPORT.replace(b'paragraph_tab, ', b'paragraph_tab as tab, ') + SHOW + LANG, self.packages),
                (PREFIX + b'#let paragraph_tab = "other"\n', self.packages),
                (PREFIX + b'#(paragraph_tab = "other")\n', self.packages),
                (IMPORT + b'#show: unknown\n' + SHOW + LANG, self.packages)):
            with self.subTest(prefix=prefix, packages=bool(packages)):
                self.assertFalse(self.direct(prefix + BODY, packages=packages)[2].edits)
        for body in (b'First $x$ tail.\n\nthis holds.\n',
                     b'First idea.\n\nthis *strong* tail.\n',
                     b'First idea.\n\n#highlight[this holds.]\n',
                     b'First idea.\n\n// comment\nthis holds.\n',
                     b'#paragraph_tab\nFirst idea.\n\n#let x = 1\nthis holds.\n'):
            with self.subTest(body=body):
                self.assertFalse(self.direct(PREFIX + body)[2].edits)

    def test_provenance_is_immutable_and_stale_or_forged_evidence_refuses(self):
        source = PREFIX + BODY
        tree, resolution, _ = self.direct(source)
        proof = resolution.paragraph_insertion_proof
        with self.assertRaises(FrozenInstanceError):
            proof.source_sha256 = 'changed'
        with self.assertRaises(FrozenInstanceError):
            self.packages[0].source = b'changed'
        for invalid in (None, ParagraphInsertionProof('stale', proof.available),
                        ParagraphInsertionProof(digest(source), frozenset({len(tree.nodes)})),
                        ParagraphInsertionProof(digest(source), frozenset({True}))):
            result = markers(tree, 'main.typ', replace(resolution, paragraph_insertion_proof=invalid),
                             allow_followup_casing=True)
            self.assertFalse(result.edits)
        self.assertFalse(markers(tree, 'main.typ', replace(resolution, source_sha256='stale'),
                                 allow_followup_casing=True).edits)
        self.assertEqual(digest(self.packages[0].source), TEXT_UTILS_SOURCE_SHA256)
        self.assertEqual(Path(self.packages[0].source_identity).read_bytes(), self.packages[0].source)

    def test_opt_in_still_rejects_real_candidate_text_reinterpretation(self):
        source = PREFIX + BODY
        tree, resolution, _ = self.direct(source)
        def changed(content, mode):
            candidate = parse(content, mode)
            target = next(i for i in candidate.nodes[0].children if candidate.nodes[i].kind == 'Text'
                          and content[candidate.nodes[i].start:candidate.nodes[i].end].startswith(b'this'))
            nodes = list(candidate.nodes)
            nodes[target] = replace(nodes[target], kind='Heading')
            return replace(candidate, nodes=tuple(nodes))
        with patch('prose_marker_rules.parse', side_effect=changed):
            result = markers(tree, 'main.typ', resolution, allow_followup_casing=True)
        self.assertFalse(result.edits)
        self.assertTrue(any('reinterpret original literal prose' in s for s in result.skips))

    def test_real_cli_modes_remain_read_only_and_never_invoke_compiler(self):
        source = PREFIX + BODY
        package_before = Path(self.packages[0].source_identity).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target, output = root / 'main.typ', root / 'previous.pdf'
            target.write_bytes(source)
            output.write_bytes(b'previous PDF')
            config = root / '.typst-lint.toml'
            config.write_bytes(b'rule-set="pde"\ninclude=["main.typ"]\n')
            compiler = root / 'bin' / 'typst'
            compiler.parent.mkdir()
            compiler.write_text('#!/bin/sh\nprintf called > "$MARKER"\nexit 91\n')
            compiler.chmod(0o700)
            marker = root / 'compiler-called'
            env = dict(os.environ, PATH=str(compiler.parent) + os.pathsep + os.environ.get('PATH', ''),
                       MARKER=str(marker), PYTHONDONTWRITEBYTECODE='1')
            original = {p: p.read_bytes() for p in (target, output, config)}
            for mode in ('check', 'dry-run', 'fix', 'compile'):
                launcher = TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')
                command = [sys.executable, str(launcher), 'fix' if mode == 'dry-run' else mode,
                           '--root', str(root), '--package-path', str((TOOL / 'tests/fixtures/reviewed-packages')), '--format', 'json']
                if mode == 'dry-run':
                    command.append('--dry-run')
                if mode == 'compile':
                    command.extend(['--', 'main.typ', 'previous.pdf'])
                run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 2, run.stderr)
                value = json.loads(run.stdout)
                edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
                self.assertEqual(plan(source, edits).candidate(),
                                 source.replace(b'this consequence', b'#paragraph_tab\nThis consequence'))
                self.assertFalse(value['applied_fixes'] or value['compiler_invoked'] or value['coverage']['complete'])
                self.assertTrue(all(e['baseline_sha256'] == digest(source) for e in value['proposed_edits']))
                self.assertTrue(all(e['repair_passes'] == 2 for e in value['proposed_edits']))
                self.assertTrue(all(p.read_bytes() == raw for p, raw in original.items()))
                self.assertFalse(marker.exists())
            self.assertEqual(Path(self.packages[0].source_identity).read_bytes(), package_before)
            # The actual config parser must preserve both independent ignores.
            for rid, expected in ((RULE_ID, source),
                                  (CAPITALIZATION, source.replace(b'this', b'#paragraph_tab\nthis'))):
                config.write_text('rule-set="pde"\n[[ignore]]\npaths=["main.typ"]\n'
                                  f'rules=["{rid}"]\nreason="intentional"\n')
                value = report(root, 'dry-run', package_path=(TOOL / 'tests/fixtures/reviewed-packages'))
                edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
                self.assertEqual(plan(source, edits).candidate(), expected)
                self.assertNotIn(rid, {owner for e in value['proposed_edits']
                                      for owner in e.get('rule_ids', [e['rule_id']])})


if __name__ == '__main__':
    unittest.main()
