"""Playbooks — pre-built workflows for common user intents.

A Playbook is the OpenBro answer to 'why ask the LLM for something we already
know how to do?' Each playbook owns:

  - a pattern of trigger phrases (regex / keyword sets)
  - a fixed sequence of tool calls
  - a response template that fills slots from tool output

When the user query matches a playbook, the agent skips the LLM loop
entirely (zero tokens, instant response). When no playbook matches, the
existing ReAct loop runs as before — playbooks are a fast path, not a
replacement.

Difference from `openbro.skills`: skills are PLUGIN packages that register
new tools (github, gmail, notion). Playbooks are PRE-BUILT WORKFLOWS that
chain existing tools for common intents. Same conceptual space, opposite
direction.
"""

from openbro.playbooks.base import Playbook, PlaybookContext, PlaybookMatch
from openbro.playbooks.registry import PlaybookRegistry

__all__ = ["Playbook", "PlaybookContext", "PlaybookMatch", "PlaybookRegistry"]
