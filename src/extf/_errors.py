"""The exceptions of the library: every refusal names each bad line it found."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LineError:
    """A bad line of a file: which file, which line (1 = the first), what is wrong."""

    file: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file}, line {self.line}: {self.message}"


class ExtfError(Exception):
    """The base of every refusal; ``errors`` names every bad line. :func:`extf.validate` and
    the writers raise it, the readers its subclass :class:`DatevFormatError`."""

    def __init__(self, errors: Sequence[LineError]) -> None:
        self.errors = list(errors)
        shown = "; ".join(str(error) for error in self.errors[:5])
        more = f" (and {len(self.errors) - 5} more)" if len(self.errors) > 5 else ""
        super().__init__(f"{len(self.errors)} bad line(s): {shown}{more}")


class DatevFormatError(ExtfError):
    """A file that is not what it claims to be."""
