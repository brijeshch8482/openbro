"""Built-in playbooks registry.

Each playbook lives in its own module under this package. `all_builtin_playbooks()`
returns the ordered list — order matters when two playbooks could match the
same input; earlier ones win on equal confidence.
"""

from __future__ import annotations


def all_builtin_playbooks() -> list[type]:
    """Return the list of built-in playbook CLASSES (not instances).

    Imported here lazily so a missing optional dep in one playbook doesn't
    break the whole registry import.
    """
    from openbro.playbooks.builtin.close_app import CloseAppPlaybook
    from openbro.playbooks.builtin.file_search import FileSearchPlaybook
    from openbro.playbooks.builtin.geo_lookup import GeoLookupPlaybook
    from openbro.playbooks.builtin.open_app import OpenAppPlaybook
    from openbro.playbooks.builtin.planner import PlannerPlaybook
    from openbro.playbooks.builtin.process_check import ProcessCheckPlaybook
    from openbro.playbooks.builtin.project_explain import ProjectExplainPlaybook
    from openbro.playbooks.builtin.system_health import SystemHealthPlaybook
    from openbro.playbooks.builtin.tech_research import TechResearchPlaybook
    from openbro.playbooks.builtin.time_now import TimeNowPlaybook

    return [
        # Most specific patterns first — they win tie-breakers against
        # broader matchers (geo > app, etc.).
        GeoLookupPlaybook,
        TimeNowPlaybook,
        SystemHealthPlaybook,
        ProcessCheckPlaybook,
        ProjectExplainPlaybook,
        CloseAppPlaybook,
        OpenAppPlaybook,
        FileSearchPlaybook,
        # Last two — context injectors that pass through to the LLM.
        # Planner adds 'plan before acting' for complex queries; tech
        # research adds real web sources for technical Q&A. Both run
        # AFTER specific playbooks because those produce final answers;
        # these only augment context for the LLM that follows.
        PlannerPlaybook,
        TechResearchPlaybook,
    ]
