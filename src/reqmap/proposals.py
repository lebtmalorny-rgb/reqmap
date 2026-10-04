"""Typed rejection of an untrusted external or model proposal."""
from typing import Literal


class ProposalError(ValueError):
    def __init__(self, kind: Literal["shape", "semantic"], violations: tuple[str, ...]):
        self.kind = kind
        self.violations = violations
        super().__init__("; ".join(violations))
