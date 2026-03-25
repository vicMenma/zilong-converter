"""
Simple in-memory user state manager.
Each user has a dict: { "mode": ..., "video_path": ..., "sub_path": ..., ... }
"""

from dataclasses import dataclass, field
from typing import Optional

# ── States ────────────────────────────────────────────────────────────────────
IDLE               = "IDLE"

# Subtitle flow
SUB_WAIT_VIDEO     = "SUB_WAIT_VIDEO"    # subtitle received, waiting for video
SUB_WAIT_FILE      = "SUB_WAIT_FILE"     # video received, waiting for subtitle
SUB_WAIT_CHOICE    = "SUB_WAIT_CHOICE"   # both ready, waiting for burn/mux choice

# Compression flow
COMP_WAIT_TARGET   = "COMP_WAIT_TARGET"  # video received, waiting for target input
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class UserSession:
    mode:       str            = IDLE
    video_path: Optional[str]  = None
    sub_path:   Optional[str]  = None
    sub_ext:    Optional[str]  = None   # ".ass" | ".srt" | ".vtt" | ".txt"
    duration:   Optional[float]= None   # seconds
    comp_mode:  Optional[str]  = None   # "size" | "resolution"
    extra:      dict           = field(default_factory=dict)

    def reset(self):
        self.mode       = IDLE
        self.video_path = None
        self.sub_path   = None
        self.sub_ext    = None
        self.duration   = None
        self.comp_mode  = None
        self.extra      = {}


_sessions: dict[int, UserSession] = {}

def get_session(user_id: int) -> UserSession:
    if user_id not in _sessions:
        _sessions[user_id] = UserSession()
    return _sessions[user_id]

def reset_session(user_id: int):
    get_session(user_id).reset()
