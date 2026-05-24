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
from core.services.state_mutation import StateMutationEngine
from core.services.verification import VerificationEngine, VerificationResult
from core.services.computer_use_verifier import (
    build_verified_payload,
    evaluate_stage,
    new_stage_evidence,
    update_stage_evidence,
)
from core.services.conversation_compressor import (
    ConversationCompressor,
    ConversationCompressionResult,
)
from core.services.context_pack_paths import (
    collect_runtime_context_paths,
    normalize_runtime_context_path,
)
from core.services.context_pack_renderer import (
    ContextPackRenderer,
    resolve_persona_turn_type,
    render_context_arbitration_block,
)
from core.services.context_pack_service import ContextPackService

__all__ = [
    "build_verified_payload",
    "collect_runtime_context_paths",
    "ContextPackRenderer",
    "ContextPackService",
    "ConversationCompressionResult",
    "ConversationCompressor",
    "evaluate_stage",
    "FileWorkEngine",
    "FollowupResolutionEngine",
    "invert_manifest",
    "is_bulk_action",
    "new_stage_evidence",
    "normalize_runtime_context_path",
    "record_manifest",
    "render_context_arbitration_block",
    "resolve_persona_turn_type",
    "RouteClarifier",
    "SearchWorkflowEngine",
    "StateMutationEngine",
    "SummaryEngine",
    "update_stage_evidence",
    "VerificationEngine",
    "VerificationResult",
]
