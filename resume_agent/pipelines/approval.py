"""What is held between producing a draft and the user approving it.

Previously the whole mutable workflow result was parked here and then fed back
into the next workflow step, which meant the approval gate carried thirty fields
of intermediate state that only two of the later stages read. A draft awaiting
approval only needs the request that produced it, the draft itself, and the
evaluation behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ApprovedDraft:
    """A tailored draft waiting on the user's decision."""

    request: Any          # TailorRequest
    outcome: Any          # TailorOutcome
    fit: Optional[Any] = None  # FitOutcome behind it

    @property
    def tailored_resume(self) -> Optional[str]:
        return getattr(self.outcome, "tailored_resume", None)

    @property
    def evaluation(self) -> Any:
        return getattr(self.outcome, "evaluation", None) or getattr(self.fit, "evaluation", None)
