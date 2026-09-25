"""System-prompt blocks for the tutor graph.

Kept as plain module-level strings (not files loaded at runtime) so that:
- imports are cheap — one lookup at boot, no I/O per turn;
- prompts version alongside the code they steer;
- unit tests can patch a specific block without a fixture directory.

Every block that used to sit inside `chat.py` as a giant f-string is here so
`response_synthesizer.py` can compose the system prompt declaratively.
"""

from .system_blocks import (
    ATTACHMENT_BLOCK,
    IMAGE_LEGEND_BLOCK,
    MERMAID_BLOCK,
    NOTES_BLOCK,
    REFUSAL_LINE,
    VIDEO_BLOCK,
    WIDGET_3D_BLOCK,
    build_system_prompt,
    mode_instruction_for,
)
from .intent_planner import build_intent_prompt

__all__ = [
    "ATTACHMENT_BLOCK",
    "IMAGE_LEGEND_BLOCK",
    "MERMAID_BLOCK",
    "NOTES_BLOCK",
    "REFUSAL_LINE",
    "VIDEO_BLOCK",
    "WIDGET_3D_BLOCK",
    "build_intent_prompt",
    "build_system_prompt",
    "mode_instruction_for",
]
