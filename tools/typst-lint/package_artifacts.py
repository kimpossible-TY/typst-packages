"""Fixed reviewed supplemental artifacts, never dependency discovery."""
from dataclasses import dataclass

@dataclass(frozen=True)
class PackageArtifact:
    relative_path: str
    source_identity: str
    source: bytes

MANNOT = '@preview/mannot:0.4.0'
CETZ = '@preview/cetz:0.4.2'
CETZ_SOURCE_SHA256 = '53380cd9de16c1f845f325e9d8be8e0f03493c2073fba1c54d546e1906a8f8ed'
CETZ_MANIFEST_SHA256 = '2ccea70cc14ad8d79897179d3c8946758e610903e59f154acdb0f9b9ff81ac02'
MANNOT_ARTIFACTS = (
    ('src/mark.typ', '86f7653d76aa4574f811c8b63a31f79b0a90c4989e24ac1277857334b6d35882'),
    ('src/annot.typ', '12ea7288aeb7378bb6cdf8747d0d94fb3828def856521540a881edf25f4d42bd'),
    ('src/util.typ', 'b88ea6e8d207c611925822d9d6ae7cb83bcf0486ba68af57807a9e4539b98583'),
)
CETZ_ARTIFACTS = (
    ('src/draw.typ', '2ab63ab5c20e875c0f12a3630bb99606e47aa076b5b94769f6930f7a24270258'),
    ('src/draw/shapes.typ', '8001363447a69292df8a5210c3963987c65e203e7d0c6981759e7d4bf21e4db1'),
    ('src/draw/transformations.typ', '98479838bee69d302d8ab394c9240c7ab1eb4794395927133350f433cfac8fc1'),
    ('src/draw/styling.typ', '83c0bbbc53e10c974776440355df41add4329436a78ad033fdcc2682f7a2e33a'),
    ('src/draw/grouping.typ', '82611e93ad2f6fccde4a313db02eb8a6aeb8d596ce959b747d4830f25d855da0'),
    ('src/coordinate.typ', 'c59ec4660d510f847d7ea245376a6eb1489db722d328c20b4d34c1fc3ff84216'),
    ('src/anchor.typ', 'bcb7edfaa727b3d5b0be4f9003761b4446a5ff96ed374d64d836fde624b60a08'),
)
SUPPLEMENTAL_ARTIFACTS = {MANNOT: MANNOT_ARTIFACTS, CETZ: CETZ_ARTIFACTS}
