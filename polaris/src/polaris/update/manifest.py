from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UpdateStatus:
    branch: str
    local_sha: str
    remote_sha: str
    up_to_date: bool
    dirty: bool = False
    message: str = ""

    @property
    def short_local(self) -> str:
        return self.local_sha[:7] if self.local_sha else "unknown"

    @property
    def short_remote(self) -> str:
        return self.remote_sha[:7] if self.remote_sha else "unknown"


@dataclass
class UpdateResult:
    success: bool
    message: str
    previous_sha: str | None = None
    current_sha: str | None = None
    restarted: bool = False
