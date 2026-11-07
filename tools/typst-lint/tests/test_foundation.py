import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import typst_lint as lint
from edit_plan import Analysis, Edit, converge, plan


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.rules = lint.load_rules()[0]['rules']

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def config(self, value):
        self.put('.typst-lint.toml', value)
        return lint.read_config(self.root, self.rules)

    def cli(self, *arguments, gate=False):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, str(TOOL / ('typst-style' if gate else 'typst-lint')),
                               *arguments, '--root', str(self.root), '--format', 'json'],
                              capture_output=True, text=True, env=env)

    def test_catalog_matches_all_normative_metadata(self):
        spec = TOOL.parents[1] / 'openspec/changes/typst-style-linter'
        expected = {}
        for pack, count in [('core', 15), ('pde', 17)]:
            text = (spec / f'specs/typst-{pack}-style/spec.md').read_text()
            sections = re.findall(r'### Requirement: (\S+)\n(.*?)(?=\n### Requirement:|\Z)', text, re.S)
            self.assertEqual(len(sections), count)
            for rid, body in sections:
                expected[rid] = {k.lower(): re.search(rf'- \*\*{k}:\*\* (.+)', body)[1] for k in ('Grade', 'Source', 'Description')}
        self.assertEqual(set(expected), {r['id'] for r in self.rules})
        policy = (spec / 'fix-policy.md').read_text()
        for rule in self.rules:
            self.assertEqual({k:rule[k] for k in expected[rule['id']]}, expected[rule['id']])
            self.assertIn(f"| `{rule['id']}` | {rule['rewrite']} | {rule['preconditions']} |", policy)

    def test_pack_counts_and_no_empty_success(self):
        for config, count in [('', 15), ('rule-set="pde"', 32)]:
            self.config(config)
            result = lint.report(self.root, 'check')
            self.assertEqual(result['status'], 2)
            self.assertEqual(len(result['coverage']['enabled']), count)
            self.assertEqual(len(result['coverage']['unsupported']), count)
            self.assertFalse(result['coverage']['complete'])
            self.assertEqual(result['coverage']['implemented'], [])

    def test_invalid_config(self):
        cases = ['rule-set="ped"', 'rule-set=[]', 'include="*.typ"', 'include=[]',
                 'include=["../*.typ"]', 'include=["/tmp/*.typ"]', 'include=["foo**bar"]',
                 'include=["?.typ"]', 'typo=true', 'entrypoint=42', 'catalog="../c.json"',
                 'ignore=[1]', '[[ignore]]\npaths=["**"]\nrules=["*"]\nreason="x"',
                 '[[ignore]]\npaths=["**"]\nrules=["core.parenthesized-scripts"]\nreason=" "',
                 '[[ignore]]\npaths=["**"]\nrules=["core.parenthesized-scripts"]\nreason="x"\nextra=true']
        for source in cases:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.config(source)

    def test_globs(self):
        for path, glob, expected in [('main.typ','**/*.typ',True), ('a/b.typ','*.typ',False),
                                      ('a/b.typ','**/*.typ',True), ('a/b/c.typ','a/**/c.typ',True),
                                      ('a/c.typ','a/**/c.typ',True), ('a/xb.typ','a/*b.typ',True),
                                      ('a.typ','**/*.md',False), ('a/b','a/**',True)]:
            self.assertEqual(lint.matches(path, glob), expected)

    def test_selection_order_and_excludes(self):
        self.put('z.typ', '$X^i$')
        self.put('a/b.typ', '$X_j$')
        self.put('generated/invalid.typ', b'\xff')
        self.put('notes.md', 'text')
        cfg = self.config('include=["**/*.typ", "*.md"]\nexclude=["generated/**"]')
        self.assertEqual([x['path'] for x in lint.collect(self.root,cfg)], ['a/b.typ','notes.md','z.typ'])

    def test_nonregular_and_invalid_utf8(self):
        self.put('bad.typ', b'\xff')
        with self.assertRaises(UnicodeError): lint.collect(self.root, lint.Config())
        self.put('bad.typ', b'ok')
        os.link(self.root/'bad.typ', self.root/'hard.typ')
        with self.assertRaises(lint.InputError): lint.collect(self.root, lint.Config())

    def test_symlinks_and_fifo(self):
        target = self.put('outside.txt', 'x')
        (self.root/'link.typ').symlink_to(target)
        with self.assertRaises(lint.InputError): lint.collect(self.root, lint.Config())
        (self.root/'link.typ').unlink()
        os.mkfifo(self.root/'pipe.typ')
        with self.assertRaises(lint.InputError): lint.collect(self.root, lint.Config())

    def test_excluded_link_directory_is_not_followed(self):
        (self.root/'vendor').symlink_to(self.root, target_is_directory=True)
        cfg = self.config('exclude=["vendor/**"]')
        self.assertEqual(lint.collect(self.root,cfg), [])
        with self.assertRaises(lint.InputError): lint.collect(self.root, lint.Config())

    def test_catalog_and_entrypoint_are_read_only(self):
        self.put('main.typ', '$x$')
        catalog = {'schema':1,'projects':{'pde':{'references':{'energy':{'page':1,'text':'Theorem 1','x':1.0,'y':2}},'ambiguous_labels':[]}}}
        path = self.put('catalog.json', json.dumps(catalog))
        self.config('entrypoint="main.typ"\ncatalog="catalog.json"')
        before = path.read_bytes()
        result = lint.report(self.root, 'check')
        self.assertEqual(result['hashes']['reference_catalog'], lint.digest(before))
        self.assertEqual(result['coverage']['optional_skips'], [])
        self.assertEqual(path.read_bytes(), before)
        for bad in [[], {}, {'schema':True,'projects':{}}, {'schema':1,'projects':{'x':{}}},
                    {'schema':1,'projects':{'x':{'references':{'x':{'page':True,'text':'x'}}}}}]:
            path.write_text(json.dumps(bad))
            with self.assertRaises(lint.InputError): lint.report(self.root,'check')

    def test_unrepresentable_catalog_coordinates_return_json_input_failure(self):
        self.config('catalog="catalog.json"')
        for axis in ('x', 'y'):
            for value in (10**400, -(10**400), float('inf'), float('nan'), True, '1'):
                with self.subTest(axis=axis, value=value):
                    ref = {'page': 1, 'text': 'Theorem 1', axis: value}
                    self.put('catalog.json', json.dumps({'schema': 1, 'projects': {
                        'pde': {'references': {'energy': ref}}}}))
                    result = self.cli('check')
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stderr, '')
                    data = json.loads(result.stdout)
                    self.assertEqual(data['status'], 2)
                    self.assertEqual(data['phase'], 'input')
                    self.assertEqual(data['error'], 'Invalid coordinate: energy')
                    self.assertFalse(data['compiler_invoked'])

    def test_missing_catalog_and_entrypoint_fail(self):
        for key in ('catalog','entrypoint'):
            self.config(f'{key}="missing.typ"')
            with self.assertRaises(OSError): lint.report(self.root,'check')

    def test_diagnostics_and_path_ignores(self):
        rule = self.rules[0]
        def finding(path, line):
            return lint.Diagnostic(rule['id'],rule['grade'],path,line,1,'example',rule['source'])
        cfg = self.config(f'[[ignore]]\npaths=["legacy/**"]\nrules=["{rule["id"]}"]\nreason="public compatibility"')
        active, hidden = lint.diagnostics([finding('z.typ',2),finding('a.typ',3),finding('legacy/x.typ',1),finding('a.typ',1)],cfg,self.rules)
        self.assertEqual([(d.path,d.line) for d in active],[('a.typ',1),('a.typ',3),('z.typ',2)])
        self.assertEqual(len(hidden),1)
        with self.assertRaises(lint.InputError):
            lint.diagnostics([dataclasses.replace(active[0],grade='bogus')],cfg,self.rules)

    def test_diagnostics_reject_disabled_pack_and_accept_composition(self):
        def finding(rid):
            rule = next(r for r in self.rules if r['id'] == rid)
            return lint.Diagnostic(rid, rule['grade'], 'main.typ', 1, 1,
                                   'example', rule['source'])
        core = finding('core.parenthesized-scripts')
        pde = finding('pde.import-order')
        with self.assertRaisesRegex(lint.InputError, 'backend mismatch: disabled rule'):
            lint.diagnostics([pde], lint.Config(), self.rules)
        # Disabled backend findings must fail before path exclusions or ignores.
        with self.assertRaisesRegex(lint.InputError, 'backend mismatch: disabled rule'):
            lint.diagnostics([pde], lint.Config(exclude=('**',)), self.rules)
        active, hidden = lint.diagnostics([pde, core], lint.Config(pack='pde'), self.rules)
        self.assertEqual(active, (core, pde))
        self.assertEqual(hidden, ())
        self.assertEqual(lint.diagnostics([core], lint.Config(), self.rules), ((core,), ()))

    def test_every_mode_is_read_only_and_fail_closed(self):
        source = self.put('main.typ', b'\xef\xbb\xbf// comment\r\n$X^i$\r\n')
        before = source.read_bytes()
        for args in [('check',), ('fix','--dry-run'), ('fix',)]:
            result = self.cli(*args)
            self.assertEqual(result.returncode,2,result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data['compiler_invoked'])
            self.assertEqual(data['applied_fixes'],[])
            self.assertEqual(source.read_bytes(),before)

    def test_gate_never_invokes_compiler_spy_or_replaces_output(self):
        spy = self.put('compiler', '#!/bin/sh\ntouch "$0.ran"\n')
        spy.chmod(0o755)
        output = self.put('previous.pdf', b'previous output')
        result = subprocess.run([str(TOOL/'typst-style'),'compile','--root',str(self.root),'--format','json','--',str(spy),'main.typ',str(output)],capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertFalse(json.loads(result.stdout)['compiler_invoked'])
        self.assertFalse(Path(str(spy)+'.ran').exists())
        self.assertEqual(output.read_bytes(),b'previous output')

    def test_malformed_config_cli_returns_structured_error(self):
        self.put('.typst-lint.toml','rule-set = [')
        result = self.cli('check')
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['phase'],'input')


class EditTests(unittest.TestCase):
    def test_coalescing_preserves_unedited_bytes(self):
        baseline = b'\xef\xbb\xbf// comment\r\n' + 'αx'.encode() + b'\r\n'
        start = baseline.index(b'x')
        e = Edit(start,start+1,b'(x)')
        p = plan(baseline,[e,e])
        self.assertEqual(p.edits,(e,))
        self.assertEqual(p.candidate(),baseline[:start]+b'(x)'+baseline[start+1:])
        with self.assertRaises(dataclasses.FrozenInstanceError): p.baseline = b''

    def test_conflicts_deferred_and_independent_edits_apply(self):
        a,b,c = Edit(0,2,b'a'),Edit(1,3,b'b'),Edit(4,5,b'c')
        p = plan(b'012345',[c,b,a])
        self.assertEqual(p.edits,(c,))
        self.assertEqual(p.deferred,(a,b))
        self.assertEqual(plan(b'x',[Edit(0,0,b'a'),Edit(0,0,b'b')]).edits,())

    def test_rejects_invalid_edits(self):
        for e in [Edit(-1,1,b''),Edit(0,99,b''),Edit(True,1,b''),Edit(1,2,b'x'),Edit(0,2,b'\xff')]:
            with self.subTest(edit=e), self.assertRaises(lint.SafetyError): plan('α'.encode(),[e])
        with self.assertRaises(lint.SafetyError): plan(bytearray(b'x'),[])

    def test_converges_retaining_preexisting_findings(self):
        def analyze(data):
            return Analysis(True,True,frozenset({'unfixable'}), (Edit(0,1,b'b'),) if data==b'a' else ())
        result = converge(b'a',analyze)
        self.assertEqual(result.content,b'b')
        self.assertEqual(result.passes,1)
        self.assertEqual(result.unresolved,frozenset({'unfixable'}))

    def test_incomplete_parse_new_findings_and_cycles_fail(self):
        analyzers = [lambda b: Analysis(False,True), lambda b: Analysis(True,False),
                     lambda b: Analysis(True,True,frozenset({'new'}) if b==b'b' else frozenset(),(Edit(0,1,b'b'),) if b==b'a' else ()),
                     lambda b: Analysis(True,True,edits=(Edit(0,1,b'b' if b==b'a' else b'a'),)),
                     lambda b: Analysis(True,True,edits=(Edit(0,0,b'x'),Edit(0,0,b'y')))]
        for analyze in analyzers:
            with self.assertRaises(lint.SafetyError): converge(b'a',analyze)

    def test_eight_pass_limit_and_second_zero_edit_check(self):
        def analyze(b):
            return Analysis(True,True,edits=(Edit(0,len(b),str(int(b)+1).encode()),) if int(b)<8 else ())
        self.assertEqual(converge(b'0',analyze).passes,8)
        with self.assertRaises(lint.SafetyError):
            converge(b'0',lambda b: Analysis(True,True,edits=(Edit(0,len(b),str(int(b)+1).encode()),)))
        count = 0
        def unstable(b):
            nonlocal count
            count += 1
            return Analysis(True,True,edits=() if count==1 else (Edit(0,1,b'b'),))
        with self.assertRaises(lint.SafetyError): converge(b'a',unstable)


if __name__ == '__main__':
    unittest.main()
