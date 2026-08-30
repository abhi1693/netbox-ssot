from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DriftSegment:
    key: str
    label: str
    count: int
    color: str
    percentage: float


@dataclass(frozen=True, slots=True)
class DriftSummary:
    missing_locally: int = 0
    different_locally: int = 0
    matching: int = 0
    needs_attention: int = 0

    @property
    def total(self) -> int:
        return self.missing_locally + self.different_locally + self.matching + self.needs_attention

    @property
    def drifted(self) -> int:
        return self.missing_locally + self.different_locally

    @property
    def alignment_percentage(self) -> float:
        return self.matching / self.total * 100 if self.total else 0.0

    @property
    def segments(self) -> tuple[DriftSegment, ...]:
        return tuple(
            DriftSegment(key, label, count, color, count / self.total * 100 if self.total else 0.0)
            for key, label, count, color in (
                ("matching", "Matching", self.matching, "success"),
                ("missing", "Missing locally", self.missing_locally, "primary"),
                ("different", "Different locally", self.different_locally, "warning"),
                ("attention", "Needs attention", self.needs_attention, "danger"),
            )
        )

    def __add__(self, other: DriftSummary) -> DriftSummary:
        return DriftSummary(
            missing_locally=self.missing_locally + other.missing_locally,
            different_locally=self.different_locally + other.different_locally,
            matching=self.matching + other.matching,
            needs_attention=self.needs_attention + other.needs_attention,
        )


__all__ = ["DriftSegment", "DriftSummary"]
