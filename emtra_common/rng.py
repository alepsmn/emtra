"""Seeded, process-independent random streams (INTERFACES section 7)."""

import struct
from hashlib import blake2b

import numpy as np


def rng(root_seed: int, stream: str, *key: object) -> np.random.Generator:
    """Generator for (root_seed, stream, *key); identical in any process."""
    digest = blake2b(repr((stream, *key)).encode(), digest_size=16).digest()
    words = struct.unpack("<4I", digest)
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([root_seed, *words])))
