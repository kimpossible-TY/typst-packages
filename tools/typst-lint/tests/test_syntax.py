"""Real parser tests require an explicit `cargo build --locked` beforehand.

No test builds/fetches the adapter. Pure protocol/client refusal tests always run.
"""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from script_rule import analyze
import syntax_client as client
import typst_lint as lint


def payload(raw=b'x'):
    return dict(schema=2, adapter_version='0.2.0', syntax_version='0.15.1', mode='markup',
                source_bytes=len(raw), source_sha256=lint.digest(raw),
                nodes=[dict(kind='Markup', start=0, end=len(raw), children=[1], errors=False, operands=[], display=None),
                       dict(kind='Text', start=0, end=len(raw), children=[], errors=False, operands=[], display=None)])


class ProtocolTests(unittest.TestCase):
    def test_known_valid_tree(self):
        tree = client.validate(payload(), b'x', 'markup')
        self.assertEqual(tree.nodes[1].kind, 'Text')
        self.assertIsInstance(tree.nodes, tuple)
        self.assertIsNone(tree.nodes[1].display)

    def test_equation_display_field_is_required_and_exactly_typed(self):
        for value in (None, 0, 1, 'true', [], {}):
            data = payload()
            data['nodes'][1].update(kind='Equation', display=value)
            with self.subTest(value=value), self.assertRaisesRegex(client.AdapterError, 'display flag'):
                client.validate(data, b'x', 'markup')
        for value in (False, True):
            data = payload()
            data['nodes'][1].update(kind='Equation', display=value)
            self.assertIs(client.validate(data, b'x', 'markup').nodes[1].display, value)
        for kind in ('Text', 'MathAttach', 'Markup'):
            for value in (False, True, 0, 'false'):
                data = payload()
                data['nodes'][1].update(kind=kind, display=value)
                with self.subTest(kind=kind, value=value), self.assertRaises(client.AdapterError):
                    client.validate(data, b'x', 'markup')
        for change in ('missing', 'extra'):
            data = payload()
            if change == 'missing': del data['nodes'][1]['display']
            else: data['nodes'][1]['block'] = True
            with self.subTest(change=change), self.assertRaises(client.AdapterError):
                client.validate(data, b'x', 'markup')

    def test_legacy_protocol_is_rejected_even_without_equations(self):
        data = payload()
        data.update(schema=1, adapter_version='0.1.0')
        for record in data['nodes']: del record['display']
        with self.assertRaisesRegex(client.AdapterError, 'version/schema mismatch'):
            client.validate(data, b'x', 'markup')

    def test_wrong_protocol_metadata(self):
        for key, value in [('schema', True), ('schema', 1), ('adapter_version','wrong'),
                           ('syntax_version','0.15.0'), ('source_bytes',2),
                           ('source_sha256','wrong'), ('mode','math'), ('nodes',[])]:
            data = payload(); data[key] = value
            with self.subTest(key=key), self.assertRaises(client.AdapterError):
                client.validate(data, b'x', 'markup')

    def test_invalid_partitions_and_links(self):
        for change in ('cycle','shared','orphan','gap','outside','bool','utf8','error','operand'):
            data = payload('α'.encode())
            if change == 'cycle': data['nodes'][1]['children'] = [0]
            elif change == 'shared': data['nodes'][0]['children'] = [1,1]
            elif change == 'orphan': data['nodes'][0]['children'] = []
            elif change == 'gap': data['nodes'][1]['start'] = 2
            elif change == 'outside': data['nodes'][1]['end'] = 3
            elif change == 'bool': data['nodes'][1]['start'] = False
            elif change == 'utf8': data['nodes'][1]['start'] = 1
            elif change == 'error': data['nodes'][1]['kind'] = 'Error'
            elif change == 'operand': data['nodes'][0]['operands'] = [{'role':'top','node':1}]
            with self.subTest(change=change), self.assertRaises(client.AdapterError):
                client.validate(data, 'α'.encode(), 'markup')

    def test_zero_length_error_is_valid_but_flagged(self):
        data = payload()
        data['nodes'].append(dict(kind='Error', start=1, end=1, children=[], errors=True, operands=[], display=None))
        data['nodes'][0]['children'] = [1,2]
        data['nodes'][0]['errors'] = True
        tree = client.validate(data, b'x', 'markup')
        self.assertTrue(tree.nodes[0].errors)
        self.assertEqual(analyze(tree,'a.typ').parse_errors, ((1,1),))

    def test_depth_limit(self):
        data = payload()
        data['nodes'] = [dict(kind='Markup', start=0, end=1, children=[i+1], errors=False, operands=[], display=None)
                         for i in range(client.MAX_DEPTH+1)]
        data['nodes'].append(dict(kind='Text', start=0, end=1, children=[], errors=False, operands=[], display=None))
        with self.assertRaises(client.AdapterError): client.validate(data,b'x','markup')

    def test_path_input_and_mode_fail_before_execution(self):
        for executable in (Path('relative'), Path('/bin/sh'), client.EXECUTABLE.parent/'other'):
            with self.subTest(executable=executable), patch.object(client.subprocess,'Popen') as popen:
                with self.assertRaises(client.AdapterError): client.parse(b'x',executable=executable)
                popen.assert_not_called()
        for raw in (bytearray(b'x'), b'x'*(client.MAX_INPUT+1), b'\xff'):
            with self.assertRaises(client.AdapterError): client.parse(raw)
        with self.assertRaises(client.AdapterError): client.parse(b'x',mode='evaluate')
        with self.assertRaises(client.AdapterError): client.parse(b'x',timeout=float('nan'))

    def test_absent_adapter_fails_closed(self):
        with patch.object(Path,'stat',side_effect=FileNotFoundError('not built')):
            with self.assertRaises(client.AdapterError): client.parse(b'x')


@unittest.skipUnless(client.EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class RealSyntaxTests(unittest.TestCase):
    def test_typed_display_equations_and_data_exclusions(self):
        source = ('Text $x$ $ x $ $ x$ $x $ $\r\nx\r\n$\n'
                  '`$ x $` // $ x $\n#let s = "$ x $"\n#let body = [$ x $]')
        tree = client.parse(source.encode())
        equations = [n for n in tree.nodes if n.kind == 'Equation']
        self.assertEqual([n.display for n in equations], [False, True, False, False, True, True])
        self.assertTrue(all(n.display is None for n in tree.nodes if n.kind != 'Equation'))
        payload = json.loads(subprocess.check_output([str(client.EXECUTABLE), 'markup'], input=b'$ x $'))
        self.assertEqual((payload['schema'], payload['adapter_version'], payload['syntax_version']),
                         (2, '0.2.0', '0.15.1'))

    def check(self, source, expected):
        raw = source.encode('utf-8')
        result = analyze(client.parse(raw), 'fixture.typ')
        self.assertEqual(result.skips, ())
        candidate = plan(raw, result.edits).candidate()
        self.assertEqual(candidate, expected.encode('utf-8'))
        final = analyze(client.parse(candidate), 'fixture.typ')
        self.assertFalse(final.parse_errors)
        self.assertEqual(final.edits, ())
        self.assertEqual(final.diagnostics, ())
        return result

    def test_real_script_shapes_exact_and_idempotent(self):
        for source, expected in [('$X^i_j$', '$X^(i)_(j)$'),
                                 ('$ X^i $','$ X^(i) $'), ('$partial^alpha$', '$partial^(alpha)$'),
                                 ('$x^y^z$', '$x^(y^(z))$'), ("$x_i'$", "$x_(i')$"),
                                 ('$x^a/b$', '$x^(a)/b$'), ('$x^(a/b)$', '$x^(a/b)$'),
                                 ('$x^(a^b)$', '$x^(a^(b))$'), ('$x_i_j$', '$x_(i_(j))$'),
                                 ('$α^β$', '$α^(β)$'), ('$x^sqrt(y)$', '$x^(sqrt(y))$'),
                                 ('$x^(i)_(j)$','$x^(i)_(j)$')]:
            with self.subTest(source=source): self.check(source, expected)

    def test_noncode_examples_untouched_and_later_real_math(self):
        prefix = '#let example = "$X^i$"\n// $X^i$\n/* $X_j$ */\n`$X^i$`\n'
        self.check(prefix+'$X^i$', prefix+'$X^(i)$')
        self.check('#let body = [Text $X^i$]', '#let body = [Text $X^(i)$]')

    def test_utf8_bom_crlf_and_positions(self):
        result = self.check('\ufeff// α\r\nText $α^β$.\r\n', '\ufeff// α\r\nText $α^(β)$.\r\n')
        self.assertEqual((result.diagnostics[0].line,result.diagnostics[0].column), (2,9))

    def test_all_parser_modes_are_syntax_only(self):
        for raw, mode, root in [(b'$x^i$','markup','Markup'), (b'let x = $x^i$','code','Code'), (b'x^i','math','Math')]:
            tree = client.parse(raw,mode)
            self.assertEqual(tree.nodes[0].kind,root)
            self.assertTrue(any(n.kind=='MathAttach' for n in tree.nodes))
        tree = client.parse(b'#import "definitely-missing.typ": *\n$x^i$')
        self.assertFalse(tree.nodes[0].errors)  # No import evaluation or filesystem lookup.

    def test_errors_block_repairs(self):
        for raw in (b'$x^$', b'#let = ', b'$x^i'):
            result = analyze(client.parse(raw),'a.typ')
            self.assertTrue(result.skips)
            self.assertEqual(result.edits, ())
        self.check('#let s = "typst-lint: ignore-next"\n$x^i$', '#let s = "typst-lint: ignore-next"\n$x^(i)$')

    def test_escaped_or_commented_operands_refused(self):
        for raw in (b'$x^#i$', b'$x^/* comment */i$'):
            result = analyze(client.parse(raw), 'a.typ')
            self.assertTrue(result.skips)
            self.assertFalse(result.edits)

    def test_protocol_bad_json_and_oversized_output(self):
        for output in (b'not JSON', b'{}', b'x'*(client.MAX_OUTPUT+1)):
            with patch.object(client, '_run', return_value=output), self.assertRaises(client.AdapterError):
                client.parse(b'x')

    def test_timeout_process_cleanup(self):
        with self.assertRaisesRegex(client.AdapterError, 'timeout'):
            client.parse(b'x'*1000, timeout=0.000000001)
        self.assertEqual(client.parse(b'x').nodes[0].kind,'Markup')

    def test_output_limit_during_streaming(self):
        with patch.object(client, 'MAX_OUTPUT', 10), self.assertRaisesRegex(client.AdapterError,'output limit'):
            client.parse(b'x')

    def test_deterministic_and_input_limit_binary(self):
        raw = b'$x^y^z$'
        first = subprocess.check_output([str(client.EXECUTABLE),'markup'],input=raw)
        second = subprocess.check_output([str(client.EXECUTABLE),'markup'],input=raw)
        self.assertEqual(first,second)
        result = subprocess.run([str(client.EXECUTABLE),'markup'],input=b'x'*(client.MAX_INPUT+1),capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertFalse(result.stdout)

    def test_cli_partial_proposals_never_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root/'main.typ'; source.write_bytes(b'$x^y^z$')
            (root/'.typst-lint.toml').write_text('rule-set="pde"\ninclude=["**/*.typ","**/*.md"]')
            (root/'guide.md').write_text('$x^i$')
            for args in [('check',), ('fix','--dry-run'), ('fix',)]:
                result = subprocess.run([sys.executable,str(TOOL/'typst-lint'),*args,'--root',str(root),'--format','json'],capture_output=True,text=True)
                self.assertEqual(result.returncode,2,result.stderr)
                report=json.loads(result.stdout)
                self.assertEqual(len(report['diagnostics']),3)
                self.assertEqual(sum(d['path'] == 'guide.md' for d in report['diagnostics']), 1)
                self.assertTrue(report['proposed_edits'])
                self.assertFalse(report['applied_fixes'])
                self.assertFalse(report['compiler_invoked'])
                self.assertFalse(report['coverage']['complete'])
                self.assertEqual(report['proposal_status'],'partial-not-applicable-transaction')
                self.assertTrue(any(f['path']=='guide.md' and f['rule_id']=='core.parenthesized-scripts'
                                    and f['status']=='evaluated' for f in report['coverage']['files']))
                self.assertTrue(any(f['path']=='guide.md' and f['status']=='not-applicable'
                                    for f in report['coverage']['files']))
                self.assertEqual((root/'guide.md').read_bytes(), b'$x^i$')
                self.assertEqual(source.read_bytes(),b'$x^y^z$')
            (root/'.typst-lint.toml').write_text('[[ignore]]\npaths=["**/*.typ"]\nrules=["core.parenthesized-scripts"]\nreason="intentional"')
            report=lint.report(root,'dry-run')
            self.assertFalse(report['diagnostics'])
            self.assertFalse(report['proposed_edits'])
            self.assertEqual(report['suppressed_count'],2)
