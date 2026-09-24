"""Document workspace (guide §3.2): the program, not the model, holds document state."""

from parserx.workspace import queries
from parserx.workspace.integrity import IntegrityReport, verify_workspace
from parserx.workspace.store import VersionConflict, Workspace, WorkspaceExists, WorkspaceLocked, WorkspaceTampered

__all__ = ["IntegrityReport", "VersionConflict", "Workspace", "WorkspaceExists", "WorkspaceLocked",
           "WorkspaceTampered", "queries", "verify_workspace"]
