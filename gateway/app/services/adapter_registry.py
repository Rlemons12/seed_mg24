from collections.abc import Callable


class AdapterRegistry:
    """Explicit allowlist for non-declarative adapters; never imports names from profile data."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable] = {}

    def register(self, adapter_id: str, factory: Callable) -> None:
        if not adapter_id or adapter_id in self._factories:
            raise ValueError("adapter ID must be nonblank and unique")
        self._factories[adapter_id] = factory

    def create(self, adapter_id: str, **kwargs):
        factory = self._factories.get(adapter_id)
        if factory is None:
            raise ValueError("adapter is not allowlisted")
        return factory(**kwargs)

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
