"""core/services — pure services imported by orchestrator and UI layers.

Modules here expose direct-call APIs with no lifecycle hooks, registries, or
background threads.  They are deterministic utilities that happen to be
large enough to live in their own files.
"""

from core.services.search_workflow import SearchWorkflowEngine
from core.services.summary import SummaryEngine

__all__ = [
    "SearchWorkflowEngine",
    "SummaryEngine",
]
