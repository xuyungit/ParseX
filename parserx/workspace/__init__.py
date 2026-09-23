"""Document workspace (guide §3.2): the program, not the model, holds document state."""

from parserx.workspace import queries
from parserx.workspace.store import VersionConflict, Workspace, WorkspaceExists, WorkspaceLocked

__all__ = ["VersionConflict", "Workspace", "WorkspaceExists", "WorkspaceLocked", "queries"]
