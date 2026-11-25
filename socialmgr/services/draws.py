"""Content-addressed storage for predictive draws (append-only table ``forecast_draws``)."""

import hashlib
import zlib

import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from socialmgr.db.models import ForecastDraws

DTYPE = "<f8"


def encode(samples: NDArray[np.float64]) -> tuple[str, bytes]:
    raw = np.ascontiguousarray(samples, dtype=DTYPE).tobytes()
    return hashlib.sha256(raw).hexdigest(), zlib.compress(raw, level=6)


def store(session: Session, samples: NDArray[np.float64], workspace_id: int) -> str:
    digest, payload = encode(samples)
    if session.get(ForecastDraws, digest) is None:
        session.add(
            ForecastDraws(
                hash=digest, n=int(samples.size), dtype=DTYPE, payload=payload, workspace_id=workspace_id
            )
        )
        session.flush()
    return digest


def load(session: Session, digest: str) -> NDArray[np.float64]:
    row = session.get(ForecastDraws, digest)
    if row is None:
        raise LookupError(f"draws {digest} not found")
    return np.frombuffer(zlib.decompress(row.payload), dtype=row.dtype).astype(np.float64)
