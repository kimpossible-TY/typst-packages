"""Only explicit temporary catalogs; no package catalog or URLs are accessed."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import reference_catalog as catalog
import package_loader
from typst_lint import InputError, digest, read_reference_catalog, report


def fixture():
    return {'schema': 1, 'projects': {'pde': {
        'references': {'energy': {'page': 1, 'text': 'Theorem 1', 'x': 2.5, 'y': 4},
                       'duplicate': {'page': 2, 'text': 'Theorem 2'}},
        'ambiguous_labels': ['duplicate'], 'pdf_url': 'https://invalid.example/never-fetch.pdf'}}}


class ReferenceCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'catalog.json'
        self.raw = json.dumps(fixture()).encode()
        self.path.write_bytes(self.raw)

    def test_snapshot_is_validated_immutable_and_hash_bound(self):
        result = catalog.load_reference_catalog(self.path)
        self.assertEqual(result.sha256, digest(self.raw))
        self.assertEqual(result.source_identity, str(self.path))
        self.assertEqual(result.membership('pde', 'energy'), 'unique')
        self.assertEqual(result.membership('pde', 'duplicate'), 'ambiguous-label')
        self.assertEqual(result.membership('pde', 'missing'), 'missing-label')
        self.assertEqual(result.membership('other', 'energy'), 'unknown-project')
        for value, field in ((result, 'sha256'), (result.projects[0], 'alias')):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(value, field, 'changed')
        self.assertIsInstance(result.projects, tuple)
        self.assertIsInstance(result.projects[0].labels, frozenset)
        self.assertIsInstance(result.projects[0].ambiguous_labels, frozenset)
        self.path.write_bytes(b'changed after snapshot')
        self.assertEqual(result.sha256, digest(self.raw))
        self.assertEqual(result.membership('pde', 'energy'), 'unique')

    def test_digest_wrapper_and_report_load_one_snapshot(self):
        self.assertEqual(read_reference_catalog(self.path), digest(self.raw))
        (self.root / '.typst-lint.toml').write_text('catalog="catalog.json"\n')
        (self.root / 'main.typ').write_text('Ordinary prose.')
        with patch.object(catalog, 'load_reference_catalog', wraps=catalog.load_reference_catalog) as load:
            value = report(self.root, 'check')
        self.assertEqual(load.call_count, 1)
        self.assertEqual(value['hashes']['reference_catalog'], digest(self.raw))
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_no_implicit_catalog_when_unconfigured(self):
        (self.root / 'projects.json').write_bytes(b'invalid, must not be read')
        with patch.object(catalog, 'load_reference_catalog', side_effect=AssertionError('no implicit lookup')):
            value = report(self.root, 'check')
        self.assertIsNone(value['hashes']['reference_catalog'])
        self.assertEqual(value['coverage']['optional_skips'][0]['check'], 'catalog-membership')

    def test_descriptor_snapshot_does_not_reopen_path(self):
        original = package_loader._Snapshot._read_bytes
        reads = []
        def capture(fd, limit, path):
            reads.append((fd, path))
            return original(fd, limit, path)
        with patch.object(package_loader._Snapshot, '_read_bytes', side_effect=capture), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('unchecked path read')):
            result = catalog.load_reference_catalog(self.path)
        self.assertEqual(result.sha256, digest(self.raw))
        self.assertEqual(len(reads), 2)  # initial read plus same-descriptor verification
        self.assertEqual(reads[0], reads[1])

    def test_duplicate_json_keys_never_select_a_target(self):
        examples = [
            '{"schema":1,"schema":1,"projects":{}}',
            '{"schema":1,"projects":{"pde":{"references":{}},"pde":{"references":{}}}}',
            '{"schema":1,"projects":{"pde":{"references":{"energy":{"page":1,"text":"A"},"energy":{"page":2,"text":"B"}}}}}',
            '{"schema":1,"projects":{"pde":{"references":{"energy":{"page":1,"page":2,"text":"A"}}}}}',
            '{"schema":1,"projects":{"pde":{"references":{},"ambiguous_labels":["x"],"ambiguous_labels":[]}}}',
        ]
        for raw in examples:
            with self.subTest(raw=raw), self.assertRaisesRegex(InputError, 'Duplicate reference catalog key'):
                catalog.parse_reference_catalog(raw.encode(), 'fixture')

    def test_existing_shape_and_coordinate_checks_are_preserved(self):
        cases = [[], {}, {'schema': True, 'projects': {}}, {'schema': 1, 'projects': []},
                 {'schema': 1, 'projects': {' ': {'references': {}}}},
                 {'schema': 1, 'projects': {'pde': {'references': [], 'ambiguous_labels': []}}}]
        for field, value in (('page', True), ('page', 0), ('page', 1.5), ('text', ''),
                             ('text', 1), ('x', True), ('x', float('inf')),
                             ('x', float('nan')), ('y', 10**400)):
            data = fixture()
            data['projects']['pde']['references']['energy'][field] = value
            cases.append(data)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(InputError):
                catalog.parse_reference_catalog(json.dumps(bad).encode(), 'fixture')
        for ambiguous in ('energy', [1], [''], [' ']):
            data = fixture()
            data['projects']['pde']['ambiguous_labels'] = ambiguous
            with self.subTest(ambiguous=ambiguous), self.assertRaises(InputError):
                catalog.parse_reference_catalog(json.dumps(data).encode(), 'fixture')

    def test_encoding_size_and_malformed_data_fail_closed(self):
        for raw in (b'\xff', b'{', b'[]', b'{"schema":1,"projects":{}}'.decode().encode('utf-16')):
            with self.subTest(raw=raw), self.assertRaises(InputError):
                catalog.parse_reference_catalog(raw, 'fixture')
        with patch.object(catalog, 'MAX_CATALOG_BYTES', len(self.raw) - 1):
            with self.assertRaises(InputError):
                catalog.load_reference_catalog(self.path)
        bom = b'\xef\xbb\xbf' + self.raw
        self.assertEqual(catalog.parse_reference_catalog(bom, 'fixture').sha256, digest(bom))

    def test_nonstandard_json_constants_in_unused_metadata_are_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            for template in ('{"schema":1,"projects":{},"extra":%s}',
                             '{"schema":1,"projects":{"pde":{"references":{},"extra":[%s]}}}'):
                raw = (template % value).encode()
                with self.subTest(raw=raw), self.assertRaisesRegex(InputError, 'Non-standard JSON'):
                    catalog.parse_reference_catalog(raw, 'fixture')

    def test_symlinks_hardlinks_and_fifo_are_rejected(self):
        link = self.root / 'linked.json'
        link.symlink_to(self.path)
        with self.assertRaises(InputError):
            catalog.load_reference_catalog(link)
        hard = self.root / 'hard.json'
        os.link(self.path, hard)
        with self.assertRaises(InputError):
            catalog.load_reference_catalog(self.path)
        fifo = self.root / 'pipe.json'
        os.mkfifo(fifo)
        with self.assertRaises(InputError):
            catalog.load_reference_catalog(fifo)
        directory_link = self.root / 'linked-directory'
        directory_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(InputError):
            catalog.load_reference_catalog(directory_link / 'catalog.json')

    def test_catalog_change_during_snapshot_fails(self):
        original = package_loader._Snapshot.verify
        def changed(snapshot):
            self.path.write_bytes(self.raw.replace(b'energy', b'changed'))
            original(snapshot)
        with patch.object(package_loader._Snapshot, 'verify', changed), self.assertRaises(InputError):
            catalog.load_reference_catalog(self.path)


if __name__ == '__main__':
    unittest.main()
