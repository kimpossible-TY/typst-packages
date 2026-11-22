"""Immutable, validated membership evidence from one explicitly supplied file.

Catalog URLs and the package's runtime projects.json are never followed. This
snapshot proves shape and membership only, not freshness or PDF correspondence.
"""
from bisect import bisect_left
from dataclasses import dataclass
import json
import math

from package_loader import PackageInputError, read_snapshot_file
from typst_lint import InputError, digest, require, strings

MAX_CATALOG_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class CatalogProject:
    alias: str
    labels: frozenset[str]
    ambiguous_labels: frozenset[str]


@dataclass(frozen=True)
class ReferenceCatalog:
    source_identity: str
    sha256: str
    projects: tuple[CatalogProject, ...]

    def membership(self, project: str, label: str) -> str:
        """No nearest-name matching, number inference or ambiguous fallback."""
        index = bisect_left(self.projects, project, key=lambda p: p.alias)
        if index == len(self.projects) or self.projects[index].alias != project:
            return 'unknown-project'
        source = self.projects[index]
        if label in source.ambiguous_labels:
            return 'ambiguous-label'
        return 'unique' if label in source.labels else 'missing-label'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'Duplicate reference catalog key: {key}')
        result[key] = value
    return result


def parse_reference_catalog(raw: bytes, source_identity: str) -> ReferenceCatalog:
    """Validate every original record before retaining membership-only data."""
    require(type(raw) is bytes and len(raw) <= MAX_CATALOG_BYTES,
            'Reference catalog must be immutable bytes within the size limit')
    require(type(source_identity) is str and bool(source_identity),
            'Reference catalog identity must be explicit')
    non_json_constants = []

    def reject_constant(value):
        # Retain a marker until shape validation so invalid coordinates keep
        # their existing precise error. No snapshot can contain this marker,
        # even when it appeared only in otherwise unused metadata.
        non_json_constants.append(value)
        return None

    try:
        # utf-8-sig permits the existing UTF-8 BOM without accepting UTF-16/32.
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, InputError):
            raise
        raise InputError(f'Malformed reference catalog: {error}') from error
    require(type(data) is dict and type(data.get('schema')) is int and data['schema'] == 1
            and type(data.get('projects')) is dict, 'Invalid reference catalog schema')
    projects = []
    for alias, project in data['projects'].items():
        require(bool(alias.strip()) and type(project) is dict, 'Invalid catalog project')
        require(type(project.get('references')) is dict, f'Missing references: {alias}')
        ambiguous = strings(project.get('ambiguous_labels', []), 'ambiguous_labels', nonempty=False)
        for label, ref in project['references'].items():
            require(bool(label.strip()) and type(ref) is dict, f'Invalid reference: {alias}/{label}')
            require(type(ref.get('page')) is int and ref['page'] > 0, f'Invalid reference page: {label}')
            require(type(ref.get('text')) is str and bool(ref['text'].strip()), f'Invalid reference text: {label}')
            for axis in ('x', 'y'):
                if axis in ref:
                    try:
                        valid = type(ref[axis]) in (int, float) and math.isfinite(ref[axis])
                    except OverflowError:
                        valid = False
                    require(valid, f'Invalid coordinate: {label}')
        projects.append(CatalogProject(alias, frozenset(project['references']), frozenset(ambiguous)))
    require(not non_json_constants, 'Non-standard JSON numeric constant in reference catalog')
    return ReferenceCatalog(source_identity, digest(raw), tuple(sorted(projects, key=lambda p: p.alias)))


def load_reference_catalog(path) -> ReferenceCatalog:
    """Read once into a bounded descriptor-verified snapshot, then validate it."""
    try:
        identity, raw = read_snapshot_file(path, MAX_CATALOG_BYTES)
    except PackageInputError as error:
        raise InputError(f'Cannot safely read reference catalog: {error}') from error
    return parse_reference_catalog(raw, identity)
