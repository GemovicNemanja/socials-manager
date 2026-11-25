"""Deterministic fake providers for tests, fixtures and demos. No network access."""

from socialmgr.providers.fake.provider import FakeOwnerProvider, FakePublicProvider

__all__ = ["FakeOwnerProvider", "FakePublicProvider"]
