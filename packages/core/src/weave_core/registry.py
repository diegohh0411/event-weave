from importlib.metadata import entry_points

from .adapters import AdapterSpec, StageAdapter
from .stages import Stage


class Registry:
    def __init__(self):
        self._adapters: dict[tuple[Stage, str], type[StageAdapter]] = {}

    def register(self, cls: type[StageAdapter]) -> type[StageAdapter]:
        self._adapters[cls.spec.stage, cls.spec.name] = cls
        return cls

    def get(self, stage: Stage, name: str) -> type[StageAdapter]:
        return self._adapters[stage, name]

    def adapters(self, stage: Stage) -> list[AdapterSpec]:
        return [cls.spec for (s, _), cls in self._adapters.items() if s == stage]

    def load_entry_points(self, group: str = 'weave.adapters') -> None:
        for entry in entry_points(group=group):
            self.register(entry.load())
