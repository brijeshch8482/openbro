"""Built-in playbooks registry.

Each playbook lives in its own module under this package. `all_builtin_playbooks()`
returns the ordered list — order matters when two playbooks could match the
same input; earlier ones win on equal confidence.

After the 2026-05-31 LLM-first refactor (commits c5faa3a / c10d840 /
this one), only DETERMINISTIC playbooks remain — workflows where the
inputs map cleanly to outputs without LLM judgement. Regex-heavy
playbooks (planner, open_app, close_app, file_search, project_explain,
tech_research) were deleted; the LLM uses the underlying tools
directly, guided by the Thinking Principles in the system prompt.
"""

from __future__ import annotations


def all_builtin_playbooks() -> list[type]:
    """Return the list of built-in playbook CLASSES (not instances).

    Imported here lazily so a missing optional dep in one playbook doesn't
    break the whole registry import.
    """
    from openbro.playbooks.builtin.geo_lookup import GeoLookupPlaybook
    from openbro.playbooks.builtin.process_check import ProcessCheckPlaybook
    from openbro.playbooks.builtin.system_health import SystemHealthPlaybook
    from openbro.playbooks.builtin.time_now import TimeNowPlaybook

    return [
        # Deterministic shortcuts only — pure workflows, no
        # regex-orchestration of LLM behavior.
        GeoLookupPlaybook,
        TimeNowPlaybook,
        SystemHealthPlaybook,
        ProcessCheckPlaybook,
    ]
