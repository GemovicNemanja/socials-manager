"""Name-to-implementation registry for the extension interfaces."""

import importlib
from collections.abc import Callable

from socialmgr.core.config import Settings

type Factory[T] = Callable[[Settings], T]


class Registry[T]:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: dict[str, Factory[T]] = {}

    def register(self, name: str) -> Callable[[Factory[T]], Factory[T]]:
        def decorator(factory: Factory[T]) -> Factory[T]:
            if name in self._factories:
                raise ValueError(f"{self.kind} {name!r} is already registered")
            self._factories[name] = factory
            return factory

        return decorator

    def resolve(self, name: str, settings: Settings) -> T:
        """Build the implementation for ``name``: a built-in name or ``module:attribute``."""
        if name in self._factories:
            return self._factories[name](settings)
        if ":" in name:
            module_name, attr = name.split(":", 1)
            factory: Factory[T] = getattr(importlib.import_module(module_name), attr)
            return factory(settings)
        known = ", ".join(sorted(self._factories)) or "none"
        raise LookupError(f"Unknown {self.kind} {name!r}. Built-ins: {known}; or use module:attribute.")

    def names(self) -> list[str]:
        return sorted(self._factories)
