"""Pure exact offline selections and single-source grammar version selection.

Historical names are immutable language choices, not aliases for the broadest
available grammar. The separate graph selection has no single-source grammar
mapping. No parsing, filesystem access or resource lookup occurs here.
"""
from types import MappingProxyType

CLOSED_CORE_SOURCE_PROFILE = 'closed-core-v1'
STRUCTURED_CORE_SOURCE_PROFILE = 'structured-core-v1'
CAPTURED_RELATIVE_SOURCE_PROFILE = 'captured-relative-core-v1'
CLOSED_CORE_GRAMMAR_VERSION = 'closed-core-markup-v1'
STRUCTURED_CORE_GRAMMAR_VERSION = 'closed-core-markup-v2'
GRAMMAR_VERSIONS = MappingProxyType({
    CLOSED_CORE_SOURCE_PROFILE: CLOSED_CORE_GRAMMAR_VERSION,
    STRUCTURED_CORE_SOURCE_PROFILE: STRUCTURED_CORE_GRAMMAR_VERSION,
})
KNOWN_SOURCE_PROFILES = frozenset(GRAMMAR_VERSIONS)
# Public offline selection is wider than the single-source grammar API. The
# graph profile has separate root/module grammars and must never enter that API.
KNOWN_OFFLINE_SOURCE_PROFILES = KNOWN_SOURCE_PROFILES | frozenset((CAPTURED_RELATIVE_SOURCE_PROFILE,))


def is_offline_source_profile(profile):
    """Whether this is an exact public offline selection, without admission."""
    return type(profile) is str and profile in KNOWN_OFFLINE_SOURCE_PROFILES


def validate_source_profile(profile):
    """Return only an exact known source profile; never normalize or alias."""
    if type(profile) is not str or profile not in KNOWN_SOURCE_PROFILES:
        raise ValueError('Unknown explicit source profile')
    return profile


def grammar_for_source_profile(profile):
    """Return the fixed grammar version for the caller-selected profile."""
    return GRAMMAR_VERSIONS[validate_source_profile(profile)]
