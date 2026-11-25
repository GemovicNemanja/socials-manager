"""MinHash near-duplicate checks (MODEL.md §7: near-duplicate posts are blocked; no embeddings in v1).

Signatures are 64 minima over word 3-shingles, each hashed with a seeded BLAKE2b, so they are stable
across processes and machines (stored on ``posts.minhash``).
"""

import hashlib
import re
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray

NUM_PERM = 64
SHINGLE = 3
NEAR_DUPLICATE = 0.8  # estimated Jaccard similarity at or above which two texts count as near-duplicates
_MAX = np.uint64(0xFFFFFFFFFFFFFFFF)


def _tokens(text: str) -> list[str]:
    text = re.sub(r"https?://\S+", " ", text.lower())
    return re.findall(r"[\w']+", text)


def shingles(text: str) -> set[str]:
    tokens = _tokens(text)
    if len(tokens) < SHINGLE:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + SHINGLE]) for i in range(len(tokens) - SHINGLE + 1)}


def signature(text: str) -> NDArray[np.uint64]:
    sig = np.full(NUM_PERM, _MAX, dtype=np.uint64)
    for sh in shingles(text):
        encoded = sh.encode()
        hashes = np.array(
            [
                int.from_bytes(
                    hashlib.blake2b(encoded, digest_size=8, salt=i.to_bytes(8, "little")).digest(), "little"
                )
                for i in range(NUM_PERM)
            ],
            dtype=np.uint64,
        )
        np.minimum(sig, hashes, out=sig)
    return sig


def to_bytes(sig: NDArray[np.uint64]) -> bytes:
    return sig.astype("<u8").tobytes()


def from_bytes(raw: bytes) -> NDArray[np.uint64]:
    return np.frombuffer(raw, dtype="<u8").astype(np.uint64)


def similarity(a: NDArray[np.uint64], b: NDArray[np.uint64]) -> float:
    if np.all(a == _MAX) or np.all(b == _MAX):
        return 0.0
    return float(np.mean(a == b))


def nearest(sig: NDArray[np.uint64], others: Iterable[tuple[int, bytes | None]]) -> tuple[int | None, float]:
    """The most similar (id, similarity) among ``others``."""
    best_id, best = None, 0.0
    for other_id, raw in others:
        if raw is None:
            continue
        s = similarity(sig, from_bytes(raw))
        if s > best:
            best_id, best = other_id, s
    return best_id, best
