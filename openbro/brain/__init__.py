"""OpenBro Brain — the v2 intelligence layer.

The Brain is OpenBro's persistent, portable, self-improving knowledge store.
It contains the user profile, semantic memory, learned skills, world facts,
and reflection logs. The agent uses it to reason BEFORE calling the LLM,
making the LLM just a reasoning tool rather than the controller.

Public API:
    from openbro.brain import Brain
    brain = Brain.load()        # auto-creates if missing
    brain.profile               # UserProfile
    brain.skills                # SkillRegistry
    brain.memory                # SemanticMemory
    brain.update()              # pull community patterns
    brain.export("backup.tar.gz")
"""

from openbro.brain.core import Brain
from openbro.brain.profile import UserProfile
from openbro.brain.storage import BrainStorage, get_brain_dir

__all__ = ["Brain", "UserProfile", "BrainStorage", "get_brain_dir"]
