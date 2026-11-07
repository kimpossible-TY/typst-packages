"""Pure, internal, bounded text-utils paragraph applicability proof.

No parser execution, filesystem/catalog loads, writes, or transaction authority.
The bridge must retain genuine capture/parser authority and run the five actual
obligations. This module never converts ordinary PARTIAL analysis into coverage.
"""
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
import re
import tomllib
import unicodedata

import closed_core
import captured_relative_core
from capitalization import WORDS, Context, StartKind, BodyKind, MarkerBinding
from static_resolver import PackageSource, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, resolve
from syntax_client import Tree, Node, MAX_INPUT, MAX_NODES
from typst_lint import SafetyError, InputError, parse_rules

SOURCE_PROFILE = 'captured-text-utils-paragraph-v1'
GRAMMAR_VERSION = 'captured-text-utils-paragraph-grammar-v1'
PARSER_CONTRACT = closed_core.PARSER_CONTRACT
IMPORT_PATH = TEXT_UTILS
SOURCE_IDENTITY = 'text-utils/0.1.2/lib.typ'
MANIFEST_IDENTITY = 'text-utils/0.1.2/typst.toml'
SOURCE_SHA256 = TEXT_UTILS_SOURCE_SHA256
SOURCE_BYTES = 5583
MANIFEST_SHA256 = 'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2'
MANIFEST_BYTES = 173
CATALOG_SHA256 = '955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826'
MAX_PARAGRAPHS = 256
MAX_CONFIG_BYTES = 65536
MAX_CATALOG_BYTES = 65536
EVALUATED_RULE_IDS = ('core.parenthesized-scripts', 'pde.paragraph-marker-gap',
    'pde.paragraph-marker-target', 'pde.paragraph-capitalization', 'pde.prose-paragraph-marker')
STRUCTURAL_REASONS = (
 ('core.scoped-function-call', 'No call, field, scope receiver or binding declaration occurs in the root; the sole reviewed package has no scope APIs.'),
 ('core.scoped-marker-tags', 'No annotation scope, mark call, label or tag argument occurs.'),
 ('core.scoped-anchor-access', 'No annotation drawing, marker declaration, anchor string call or drawing call occurs.'),
 ('core.scoped-reference-access', 'No reference, generated scoped tag/name or reference call occurs.'),
 ('core.local-overlay-api', 'No local scope, raw annot-cetz call or overlay application occurs.'),
 ('core.annotation-in-display', 'No annotation/overlay call occurs; every equation is explicitly inline.'),
 ('core.annotation-marker-bounds', 'No overlay, overlay target, scoped marker or label occurs.'),
 ('core.unique-scope-namespace', 'The exhaustive root and exact dependency-free package contain no scope creation or scope namespace API; no additional graph source exists.'),
 ('core.project-ref-arguments', 'No project-ref binding or call occurs in the root or reviewed package.'),
 ('core.project-ref-catalog-target', 'No project-ref call or project/label target occurs, regardless of external catalogs.'),
 ('core.single-book-show', 'The sole import is text-utils, not a math-book helper; its using-shared-math-book-helper precondition is false.'),
 ('core.no-duplicate-book-reset', 'The sole show applies paragraph tabs; no book owner or math-block-reset application occurs.'),
 ('core.no-set-theme', 'No root set rule or custom theme helper application occurs.'),
 ('core.theme-in-context', 'No root closure, reusable helper, theme binding or theme-resolution call occurs; unused reviewed package closures are never invoked.'),
 ('pde.import-order', 'There is exactly one named text-utils import; no Styles/styles.typ, local figure or annotation import ordering pair exists.'),
 ('pde.no-redundant-facade-import', 'No styles, mannot_utils or local_tags facade import occurs.'),
 ('pde.continuation-line', 'Every prose paragraph occupies exactly one physical source line; no adjacent prose sentences span source lines.'),
 ('pde.display-continuation-gap', 'Every admitted equation has typed display=False; no display/prose boundary exists.'),
 ('pde.proof-trailing-gap', 'No proof binding, proof call, content block or proof-ending boundary occurs.'),
 ('pde.footnote-trailing-gap', 'No footnote binding, call, content body or footnote-ending line occurs.'),
 ('pde.prose-footnote-punctuation', 'No prose footnote call occurs.'),
 ('pde.aligned-footnote-spacer', 'No display, alignment token, footnote call or dots_space binding occurs.'),
 ('pde.unaligned-footnote-no-spacer', 'No display, footnote call or dots_space binding occurs.'),
 ('pde.prose-footnote-no-spacer', 'No prose footnote or dots_space binding/use occurs.'),
 ('pde.standalone-display', 'Every admitted equation has typed display=False; no display delimiter occurs.'),
 ('pde.text-only-highlight', 'No highlight/highlighted binding is imported or called; the unused reviewed package export is not invoked.'),
 ('pde.theme-cancelmark', 'No math call, cancel call, cancelmark binding or facade occurs.'),
 ('pde.flowbox-colored-border', 'No flowbox implementation or flowbox closure/call/border/stroke argument occurs in the selected root or sole reviewed package.'),
)
NOT_APPLICABLE_RULE_IDS = tuple(r for r, _ in STRUCTURAL_REASONS)
RULE_IDS = EVALUATED_RULE_IDS + NOT_APPLICABLE_RULE_IDS
_ISSUER = object()
_HSPACE = re.compile(rb'[ \t]*\Z')
_WS = re.compile(rb'[ \t]*(?:\r?\n[ \t]*)*\Z')
_GAP = re.compile(rb'[ \t]*\r?\n[ \t]*\Z')

class CapturedTextUtilsParagraphError(SafetyError, InputError):
    pass
class CapturedTextUtilsParagraphSyntaxError(CapturedTextUtilsParagraphError):
    pass
class CapturedTextUtilsParagraphAuthorityError(CapturedTextUtilsParagraphError):
    pass

def _require(ok, message):
    if not ok:
        raise CapturedTextUtilsParagraphAuthorityError(message)
def _syntax(ok, message):
    if not ok:
        raise CapturedTextUtilsParagraphSyntaxError(message)
def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

@dataclass(frozen=True, slots=True)
class ParagraphConfig:
    target_path: str
    entrypoint: str | None
    language: str
    source_sha256: str

def canonical_path(path):
    try:
        result = captured_relative_core.canonical_path(path)
        _require(not any(c in path for c in '*?[]{}'), 'Target must be literal, not a glob')
        return result
    except captured_relative_core.CapturedRelativeCoreError as error:
        raise CapturedTextUtilsParagraphAuthorityError(str(error)) from error

def validate_config(raw, target_path):
    canonical_path(target_path)
    _require(type(raw) is bytes and len(raw) <= MAX_CONFIG_BYTES, 'Bounded explicit config bytes required')
    try:
        data = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Invalid paragraph configuration') from error
    _require(type(data) is dict and {'rule-set','include','capitalization'} <= set(data)
        and set(data) <= {'rule-set','include','capitalization','entrypoint'}, 'Unsupported paragraph configuration keys')
    _require(type(data['rule-set']) is str and data['rule-set'] == 'pde', 'Explicit pde rule-set required')
    _require(type(data['include']) is list and len(data['include']) == 1
        and type(data['include'][0]) is str and data['include'][0] == target_path, 'Include must name exactly the canonical target')
    _require('entrypoint' not in data or (type(data['entrypoint']) is str and data['entrypoint'] == target_path), 'Entrypoint must match target')
    cap = data['capitalization']
    _require(type(cap) is dict and set(cap) == {'language'}
        and type(cap['language']) is str and cap['language'] == 'en', 'Only explicit capitalization.language=en is admitted')
    return ParagraphConfig(target_path, data.get('entrypoint'), 'en', _sha(raw))

def _catalog(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_CATALOG_BYTES and _sha(raw) == CATALOG_SHA256, 'Exact reviewed full catalog required')
    try:
        data, _ = parse_rules(raw)
    except (ValueError, UnicodeError, TypeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Invalid reviewed catalog') from error
    ids = tuple(r['id'] for r in data['rules'])
    _require(len(ids) == 33 and len(set(RULE_IDS)) == 33 and set(ids) == set(RULE_IDS)
        and sum(i.startswith('core.') for i in ids) == 15
        and sum(i.startswith('pde.') for i in ids) == 18, 'Reviewed 15 core + 18 PDE rule matrix disagrees')
    return tuple((r['id'], _sha(json.dumps(r, sort_keys=True, separators=(',',':')).encode())) for r in data['rules'])

def _validated(tree, path):
    try:
        return captured_relative_core._validate_tree(tree, path)
    except captured_relative_core.CapturedRelativeSyntaxError as error:
        raise CapturedTextUtilsParagraphSyntaxError(str(error)) from error
    except (captured_relative_core.CapturedRelativeCoreError, AttributeError, TypeError, ValueError, KeyError, RuntimeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Invalid bounded immutable original Tree') from error

def _fingerprint(records):
    return _sha(json.dumps(records, sort_keys=True, separators=(',',':')).encode())

@dataclass(frozen=True, slots=True)
class PackageIdentity:
    import_path: str
    source_identity: str
    source_sha256: str
    source_bytes: int
    manifest_identity: str
    manifest_sha256: str
    manifest_bytes: int
    tree_sha256: str
    nodes: tuple[Node, ...]
    invoked_body_span: tuple[int, int]
    invoked_body_sha256: str
    exports: tuple[tuple[str, str], ...]

def _canonical_package_identity(value):
    return (len(value) <= 4096 and value.startswith('/') and value.endswith('/local/'+SOURCE_IDENTITY)
        and '\\' not in value and len(value.encode('utf-8')) <= 4096
        and all(p not in ('','.','..') for p in value.split('/')[1:])
        and not any(ord(c)<32 or ord(c)==127 for c in value))

def certify_package(package_source, manifest_bytes, package_tree):
    """Bind exact reviewed bytes/tree, separately from capture initializer proof.

    The invoked lib.typ:11-22 closure is reviewed independently: two fixed regex
    show rules inspect captured text, call upper/h(1.5em), and emit body. No file
    read, external operation or persistent mutation occurs. Unused exports are
    initializer evidence only and gain no call authority from this review.
    """
    from static_resolver import TEXT_UTILS_EXPORTS
    try:
        _require(type(package_source) is PackageSource and type(package_source.import_path) is str
            and package_source.import_path == IMPORT_PATH and type(package_source.source_identity) is str
            and _canonical_package_identity(package_source.source_identity) and type(package_source.source) is bytes
            and len(package_source.source) == SOURCE_BYTES and _sha(package_source.source) == SOURCE_SHA256
            and type(package_source.artifacts) is tuple and not package_source.artifacts,
            'Exact reviewed genuine PackageSource required')
        _require(type(manifest_bytes) is bytes and len(manifest_bytes) == MANIFEST_BYTES
            and _sha(manifest_bytes) == MANIFEST_SHA256, 'Exact reviewed text-utils manifest required')
        metadata = tomllib.loads(manifest_bytes.decode())['package']
        _require((metadata['name'],metadata['version'],metadata['entrypoint']) == ('text-utils','0.1.2','lib.typ'), 'Package manifest metadata changed')
        records = _validated(package_tree, 'text-utils/0.1.2/lib.typ')
        _require(package_tree.source == package_source.source, 'Package Tree/source mismatch')
        lines = package_source.source.splitlines(keepends=True)
        start, end = sum(map(len, lines[:10])), sum(map(len, lines[:22]))
        invoked = package_source.source[start:end]
        _require(invoked.startswith(b'#let apply-paragraph-tabs(body) = {\n') and invoked.endswith(b'  body\n}\n'), 'Reviewed call body changed')
        return PackageIdentity(IMPORT_PATH,package_source.source_identity,SOURCE_SHA256,SOURCE_BYTES,
            MANIFEST_IDENTITY,MANIFEST_SHA256,MANIFEST_BYTES,_fingerprint(records),package_tree.nodes,
            (start,end),_sha(invoked),TEXT_UTILS_EXPORTS)
    except CapturedTextUtilsParagraphError:
        raise
    except (AttributeError, TypeError, KeyError, ValueError, UnicodeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Malformed reviewed package evidence') from error

@dataclass(frozen=True, slots=True)
class Statement:
    hash_node: int
    node: int
    span: tuple[int,int]
    bytes: bytes
    bound_nodes: tuple[int,...]
@dataclass(frozen=True, slots=True)
class Paragraph:
    ordinal: int
    span: tuple[int,int]
    content_nodes: tuple[int,...]
    first_node: int
    first_span: tuple[int,int]
    first_word: bytes
    marker_nodes: tuple[int,int] | None
    marker_span: tuple[int,int] | None
    marker_gap_span: tuple[int,int] | None
    edit_start: int | None
    replacement: bytes
@dataclass(frozen=True, slots=True)
class Boundary:
    ordinal: int
    previous_paragraph: int
    next_paragraph: int
    gap_span: tuple[int,int]
    gap_bytes: bytes
    marker_nodes: tuple[int,int]
    marker_span: tuple[int,int]
    first_node: int
@dataclass(frozen=True, slots=True)
class RuleObligation:
    rule_id: str
    catalog_record_sha256: str
    disposition: str
    reason: str
    source_sha256: str
    tree_sha256: str
@dataclass(frozen=True, slots=True, init=False)
class CapturedTextUtilsParagraphProof:
    source_profile: str
    grammar_version: str
    parser_contract: tuple[str,...]
    target_path: str
    source_sha256: str
    source_bytes: int
    tree_sha256: str
    nodes: tuple[Node,...]
    config: ParagraphConfig
    catalog_sha256: str
    package: PackageIdentity
    import_statement: Statement
    show_statement: Statement
    paragraphs: tuple[Paragraph,...]
    boundaries: tuple[Boundary,...]
    contexts: tuple[Context,...]
    evaluated_rule_ids: tuple[str,...]
    not_applicable_rule_ids: tuple[str,...]
    structural_reasons: tuple[tuple[str,str],...]
    obligations: tuple[RuleObligation,...]
    _issuer: object = field(repr=False, compare=False)
    def __init__(self, *args, **kwargs):
        raise TypeError('Paragraph proof is privately issued by certify')

class _Grammar(closed_core._Grammar):
    def __init__(self, tree, path, package):
        super().__init__(tree, 'structured-core-grammar-v1')
        self.path, self.package = path, package
        self.paragraphs, self.boundaries = [], []
        self.ids = tree.nodes[0].children
        self.position = 0
    def ws(self, i):
        n = self.nodes[i]
        text = self.token(i,n.kind)
        _syntax(n.kind in ('Space','Parbreak') and _WS.fullmatch(text.encode()) is not None, 'Only literal LF/CRLF whitespace is admitted')
    def trivia(self):
        while self.position < len(self.ids) and self.nodes[self.ids[self.position]].kind in ('Space','Parbreak'):
            self.ws(self.ids[self.position]); self.position += 1
    def standalone(self, start, end):
        raw = self.tree.source
        line = raw.rfind(b'\n',0,start)+1
        if line == 0 and raw.startswith(b'\xef\xbb\xbf'):
            line = 3
        _syntax(_HSPACE.fullmatch(raw[line:start]) is not None, 'Statement/marker must begin on its own physical line')
        finish = raw.find(b'\n',end)
        suffix = raw[end:finish] if finish >= 0 else raw[end:]
        if finish >= 0 and suffix.endswith(b'\r'): suffix = suffix[:-1]
        _syntax(_HSPACE.fullmatch(suffix) is not None, 'Statement/marker must end on its own physical line')
    def blank_before(self, start):
        raw = self.tree.source
        line = raw.rfind(b'\n',0,start)+1
        previous = raw.rfind(b'\n',0,max(0,line-1))+1
        gap = raw[previous:line]
        _syntax(gap.endswith(b'\n') and _WS.fullmatch(gap) is not None and gap.count(b'\n') == 1, 'Marker requires an immediately preceding terminated whitespace-only blank line')
    def parts(self, i, kind):
        n = self.enter(i)
        _syntax(n.kind == kind and bool(n.children), f'Expected exhaustive {kind}')
        result = []
        for child in n.children:
            if self.nodes[child].kind == 'Space':
                _syntax(_HSPACE.fullmatch(self.raw(child).encode()) is not None, 'Statements must be single-line')
                self.space(child)
            else: result.append(child)
        return tuple(result)
    def statement(self, kind):
        _syntax(self.position+1 < len(self.ids), f'Missing {kind}')
        h,i = self.ids[self.position:self.position+2]
        self.token(h,'Hash','#')
        _syntax(self.nodes[h].end == self.nodes[i].start, 'Hash must be adjacent to statement')
        self.standalone(self.nodes[h].start,self.nodes[i].end)
        parts = self.parts(i,kind)
        bound = ()
        if kind == 'ModuleImport':
            _syntax(len(parts) == 4,'Only one exact direct named package import is admitted')
            self.token(parts[0],'Import','import'); self.token(parts[1],'Str','"'+IMPORT_PATH+'"'); self.token(parts[2],'Colon',':')
            items = self.parts(parts[3],'ImportItems')
            _syntax(len(items) == 3,'Exactly paragraph_tab then apply-paragraph-tabs must be imported')
            names=[]
            for j,name in ((items[0],'paragraph_tab'),(items[2],'apply-paragraph-tabs')):
                item = self.parts(j,'ImportItemPath')
                _syntax(len(item) == 1,'No import alias/field path is admitted')
                self.token(item[0],'Ident',name); names.append(item[0])
            self.token(items[1],'Comma',','); bound=tuple(names)
        else:
            _syntax(len(parts)==3,'Exactly one direct root show is required')
            self.token(parts[0],'Show','show'); self.token(parts[1],'Colon',':'); self.token(parts[2],'Ident','apply-paragraph-tabs'); bound=(parts[2],)
        self.position += 2
        start,end=self.nodes[h].start,self.nodes[i].end
        return Statement(h,i,(start,end),self.tree.source[start:end],bound)
    def text(self, i):
        text=self.token(i,'Text')
        _syntax(bool(text) and '\n' not in text and '\r' not in text and all(
            c.isprintable() and c not in closed_core._PROSE_META and unicodedata.category(c) != 'Co' for c in text), 'Only literal nonprivate single-line prose is admitted')
    def run(self):
        self.enter(0)
        if self.ids and self.nodes[self.ids[0]].kind=='Text' and self.raw(self.ids[0])=='\ufeff':
            self.token(self.ids[0],'Text','\ufeff'); self.position=1
        self.trivia(); imported=self.statement('ModuleImport')
        before=self.position; self.trivia()
        _syntax(self.position>before and b'\n' in self.tree.source[imported.span[1]:self.nodes[self.ids[self.position]].start] if self.position<len(self.ids) else False, 'Import and show require separate physical lines')
        shown=self.statement('ShowRule')
        while self.position<len(self.ids):
            self.trivia()
            if self.position == len(self.ids): break
            _require(len(self.paragraphs) < MAX_PARAGRAPHS, 'Paragraph count budget exceeded')
            start_id=self.ids[self.position]; marker_nodes=marker_span=marker_gap=None
            if self.nodes[start_id].kind=='Hash':
                _syntax(self.position+2<len(self.ids),'Incomplete paragraph marker')
                h,i=self.ids[self.position:self.position+2]
                self.token(h,'Hash','#'); self.token(i,'Ident','paragraph_tab')
                _syntax(self.nodes[h].end==self.nodes[i].start,'Marker hash must be adjacent')
                marker_nodes=(h,i); marker_span=(self.nodes[h].start,self.nodes[i].end)
                self.standalone(*marker_span); self.blank_before(marker_span[0]); self.position+=2
                gap=self.ids[self.position]
                _syntax(self.nodes[gap].kind=='Space' and _GAP.fullmatch(self.raw(gap).encode()) is not None,'Marker must be adjacent to first prose line by exactly one LF/CRLF')
                self.ws(gap); self.position+=1
            _syntax(self.position<len(self.ids),'Marker has no paragraph')
            first_id=self.ids[self.position]; first=self.nodes[first_id]
            _syntax(first.kind=='Text','Paragraph must start with direct literal Text')
            _syntax(marker_nodes is not None or not self.paragraphs,'Every after-existing-prose boundary requires its own canonical marker')
            if marker_span: marker_gap=(marker_span[1],first.start)
            else:
                _syntax(b'\n' in self.tree.source[shown.span[1]:first.start], 'Show must precede prose on a separate physical line')
            content=[]; start=first.start; end=start
            while self.position<len(self.ids):
                i=self.ids[self.position]; n=self.nodes[i]
                if n.kind in ('Space','Parbreak') and b'\n' in self.tree.source[n.start:n.end]: break
                if n.kind=='Text': self.text(i)
                elif n.kind=='Space': self.ws(i)
                elif n.kind=='Equation':
                    _syntax(n.display is False and b'\n' not in self.tree.source[n.start:n.end] and b'\r' not in self.tree.source[n.start:n.end], 'Only single-line inline math is admitted')
                    self.equation(i)
                else: raise CapturedTextUtilsParagraphSyntaxError(f'Unsupported paragraph token {n.kind}')
                content.append(i); end=n.end; self.position+=1
            word=b''; word_end=first.start; edit=None; replacement=b''
            if marker_nodes:
                match=re.match(rb'[A-Za-z]+',self.tree.source[first.start:first.end])
                _syntax(match is not None,'Marked starter must be an ASCII allowlisted word')
                word=match[0]; word_end=first.start+len(word)
                _syntax(word.lower() in WORDS and word_end<len(self.tree.source)
                    and self.tree.source[word_end:word_end+1] in (b' ',b'\t',b'\r',b'\n'), 'Marked starter must be a complete allowlisted ASCII word followed by whitespace')
                clean=word==word[:1].upper()+word[1:].lower() or word.isupper()
                _syntax(clean or word.islower(),'Mixed-case marked starter is unsupported')
                if not clean: edit=first.start; replacement=word[:1].upper()
            p=Paragraph(len(self.paragraphs),(start,end),tuple(content),first_id,(first.start,word_end),word,marker_nodes,marker_span,marker_gap,edit,replacement)
            if self.paragraphs:
                previous=self.paragraphs[-1]
                gap=(previous.span[1],marker_span[0])
                _syntax(_WS.fullmatch(self.tree.source[gap[0]:gap[1]]) is not None and self.tree.source[gap[0]:gap[1]].count(b'\n')>=2,'After-prose marker needs a blank source boundary')
                self.boundaries.append(Boundary(len(self.boundaries),previous.ordinal,p.ordinal,gap,self.tree.source[gap[0]:gap[1]],marker_nodes,marker_span,first_id))
            self.paragraphs.append(p)
        _syntax(any(p.marker_nodes is not None for p in self.paragraphs),'At least one marked literal prose paragraph is required')
        _syntax(len(self.visited)==len(self.nodes),'Every original AST descendant/token must be consumed')
        for n in self.nodes:
            for _,operand in n.operands:
                o=self.nodes[operand]
                _syntax(o.kind=='Math' and bool(o.children) and self.nodes[o.children[0]].kind=='LeftParen' and self.nodes[o.children[-1]].kind=='RightParen','Inline scripts must already be clean and parenthesized')
        resolution=resolve(self.tree,fallback_language='en',packages=(self.package,))
        _syntax(not resolution.skips,'Exact root resolution must have no skipped constructs')
        for i,name in (*((p.marker_nodes[1],'paragraph_tab') for p in self.paragraphs if p.marker_nodes), (shown.bound_nodes[0],'apply-paragraph-tabs')):
            binding=resolution.binding_for(i)
            _syntax(binding is not None and binding.kind=='api' and binding.name==name and binding.package==IMPORT_PATH
                and binding.source_identity==self.package.source_identity and binding.source_sha256==SOURCE_SHA256,'Imported helper identity must resolve to reviewed package')
        for p in self.paragraphs:
            facts=resolution.facts[p.first_node]
            _syntax(facts.prose_safe and not facts.deferred and facts.effective_language=='en','Known root prose/language ownership required')
        return imported,shown,tuple(self.paragraphs),tuple(self.boundaries)

def certify(tree, path, *, config_bytes, catalog_bytes, package_source, manifest_bytes, package_tree, source_profile=SOURCE_PROFILE):
    _require(type(source_profile) is str and source_profile==SOURCE_PROFILE,'Explicit internal paragraph profile required')
    config=validate_config(config_bytes,path); catalog=_catalog(catalog_bytes)
    records=_validated(tree,path); package=certify_package(package_source,manifest_bytes,package_tree)
    try:
        imported,shown,paragraphs,boundaries=_Grammar(tree,path,package_source).run()
    except CapturedTextUtilsParagraphError: raise
    except closed_core.ClosedCoreError as error:
        raise CapturedTextUtilsParagraphSyntaxError(str(error)) from error
    except (AttributeError,TypeError,ValueError,KeyError,IndexError,RuntimeError) as error:
        raise CapturedTextUtilsParagraphAuthorityError('Malformed paragraph representation') from error
    sha,fingerprint=_sha(tree.source),_fingerprint(records)
    contexts=tuple(Context(sha,path,3 if tree.source.startswith(b'\xef\xbb\xbf') else 0,len(tree.source),
        p.marker_span[0],p.marker_span[1],p.first_span[0],p.first_span[1],p.first_word,
        StartKind.LITERAL_WORD,BodyKind.ORDINARY_PROSE,MarkerBinding.PARAGRAPH_TAB,'en')
        for p in paragraphs if p.marker_nodes is not None)
    reasons=dict(STRUCTURAL_REASONS)
    actual={
      'core.parenthesized-scripts':'Later actual script analyzer must confirm no diagnostics, edits, skips, parse errors or suppression.',
      'pde.paragraph-marker-gap':'Later actual marker-gap analyzer must confirm every marker has its preceding blank line.',
      'pde.paragraph-marker-target':'Later actual marker-target analyzer must confirm every marker targets direct prose, never a heading/display.',
      'pde.paragraph-capitalization':'Later actual capitalization analyzer alone may propose the recorded first-byte edits; final result must be clean.',
      'pde.prose-paragraph-marker':'Later independently invoked exhaustive bounded boundary checker must confirm every after-existing-prose boundary has its immediately associated marker.',
    }
    obligations=tuple(RuleObligation(r,h,'requires-actual-evaluation' if r in actual else 'structurally-inapplicable',actual.get(r,reasons.get(r)),sha,fingerprint) for r,h in catalog)
    values=dict(source_profile=SOURCE_PROFILE,grammar_version=GRAMMAR_VERSION,parser_contract=PARSER_CONTRACT,target_path=path,
        source_sha256=sha,source_bytes=len(tree.source),tree_sha256=fingerprint,nodes=tree.nodes,config=config,catalog_sha256=CATALOG_SHA256,
        package=package,import_statement=imported,show_statement=shown,paragraphs=paragraphs,boundaries=boundaries,contexts=contexts,
        evaluated_rule_ids=EVALUATED_RULE_IDS,not_applicable_rule_ids=NOT_APPLICABLE_RULE_IDS,structural_reasons=STRUCTURAL_REASONS,obligations=obligations,_issuer=_ISSUER)
    proof=object.__new__(CapturedTextUtilsParagraphProof)
    for name,value in values.items(): object.__setattr__(proof,name,value)
    return proof

def _same_exact(left,right):
    if type(left) is not type(right): return False
    if type(right) is tuple:
        return len(left)==len(right) and all(_same_exact(a,b) for a,b in zip(left,right))
    if is_dataclass(right):
        try: return all(_same_exact(getattr(left,f.name),getattr(right,f.name)) for f in fields(right))
        except AttributeError: return False
    return left==right

def verify_proof(proof, tree, path, **authority):
    _require(type(proof) is CapturedTextUtilsParagraphProof and getattr(proof,'_issuer',None) is _ISSUER,'Unissued paragraph proof')
    try: expected=certify(tree,path,**authority)
    except CapturedTextUtilsParagraphError as error:
        raise CapturedTextUtilsParagraphAuthorityError('Paragraph proof authority changed: '+str(error)) from error
    _require(_same_exact(proof,expected),'Paragraph proof differs from explicit selected source/tree/config/package/catalog')

def check_boundaries(tree, path, **authority):
    """Actual exhaustive bounded boundary obligation, separate from generic analyzer.

    Rewalks every root/descendant token, derives every prose paragraph and checks
    every after-existing-prose boundary. Never filters a generic partial skip.
    Returns source-bound boundary records, not an ordinary COMPLETE status.
    """
    return certify(tree,path,**authority).boundaries

def compare_candidate(before, after, *, before_tree, after_tree, path, **authority):
    """Verify both proofs and exact one-byte capitalization-only candidate."""
    verify_proof(before,before_tree,path,**authority); verify_proof(after,after_tree,path,**authority)
    _require(_same_exact(before.nodes,after.nodes),'Candidate changed AST kinds/spans/edges/operands/display metadata')
    expected=bytearray(before_tree.source)
    for p in before.paragraphs:
        if p.edit_start is not None: expected[p.edit_start:p.edit_start+1]=p.replacement
    _require(bytes(expected)==after_tree.source,'Candidate differs beyond exact recorded first ASCII bytes')
    _require(all(p.edit_start is None for p in after.paragraphs),'Candidate has remaining capitalization edits')
    _require(_same_exact(before.import_statement,after.import_statement) and _same_exact(before.show_statement,after.show_statement)
        and _same_exact(before.boundaries,after.boundaries) and _same_exact(before.package,after.package),'Candidate changed statement/boundary/package ownership')

certify_captured_text_utils_paragraph = certify
verify_captured_text_utils_paragraph = verify_proof

@dataclass(frozen=True, slots=True)
class BoundaryResult:
    source_sha256: str
    boundaries: tuple[Boundary,...]
    diagnostics: tuple = ()
    edits: tuple = ()
    skips: tuple = ()
    parse_errors: tuple = ()
    suppressed: tuple = ()

def analyze_boundaries(tree, path, **authority):
    """Actually execute the exhaustive bounded prose-boundary checker."""
    proof=certify(tree,path,**authority)
    return BoundaryResult(proof.source_sha256,proof.boundaries)
