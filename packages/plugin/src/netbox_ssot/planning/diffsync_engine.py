from __future__ import annotations

from diffsync import Adapter, Diff
from diffsync.enum import DiffSyncFlags


class ComparisonOnlyDiffSyncEngine:
    """Narrow DiffSync gateway that deliberately exposes no synchronization operation."""

    flags = DiffSyncFlags.SKIP_UNMATCHED_DST

    def compare(self, source: Adapter, target: Adapter) -> Diff:
        return source.diff_to(target, flags=self.flags)
