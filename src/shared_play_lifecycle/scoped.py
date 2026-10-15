"""Call-local exact observations for the one-account Dev lifecycle worker."""
from dataclasses import dataclass,field


@dataclass(frozen=True)
class ScopedTarget:
    row: dict=field(repr=False)
    guards: tuple=field(repr=False)
