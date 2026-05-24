"""core/services — pure services imported by orchestrator and UI layers.

Modules here expose direct-call APIs with no lifecycle hooks, registries, or
background threads.  They are deterministic utilities that happen to be
large enough to live in their own files.
"""

from core.services.file_work import FileWorkEngine
from core.services.followup_resolution import FollowupResolutionEngine
from core.services.route_clarity import RouteClarifier
from core.services.search_workflow import SearchWorkflowEngine
from core.services.summary import SummaryEngine
from core.services.rollback_engine import (
    invert_manifest,
    is_bulk_action,
    record_manifest,
)
from core.services.verification import VerificationEngine, VerificationResult

__all__ = [
    "FileWorkEngine",
    "FollowupResolutionEngine",
    "invert_manifest",
    "is_bulk_action",
    "record_manifest",
    "RouteClarifier",
    "SearchWorkflowEngine",
    "SummaryEngine",
    "VerificationEngine",
    "VerificationResult",
]
