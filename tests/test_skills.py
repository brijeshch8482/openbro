"""Tests for skills system."""

from openbro.skills.base import BaseSkill
from openbro.skills.builtin.github import GitHubSkill, GitHubTool
from openbro.skills.builtin.gmail import GmailSkill, GmailTool
from openbro.skills.builtin.google_calendar import GoogleCalendarSkill, GoogleCalendarTool
from openbro.skills.builtin.notion import NotionSkill, NotionTool
from openbro.skills.builtin.youtube import YouTubeSkill, YouTubeTool
from openbro.skills.registry import SkillRegistry


def test_base_skill_get_nested():
    cfg = {"skills": {"github": {"token": "abc"}}}
    assert BaseSkill._get_nested(cfg, "skills.github.token") == "abc"
    assert BaseSkill._get_nested(cfg, "skills.notion.token") is None
    assert BaseSkill._get_nested(cfg, "missing.key") is None


def test_github_skill_anonymous_works():
    skill = GitHubSkill(config={})
    assert skill.is_configured() is True
    tools = skill.tools()
    assert len(tools) == 1
    assert tools[0].name == "github"


def test_github_tool_create_issue_requires_token():
    tool = GitHubTool(token=None)
    result = tool.run(action="create_issue", repo="x/y", title="t")
    assert "token required" in result.lower()


def test_github_tool_unknown_action():
    tool = GitHubTool()
    assert "Unknown action" in tool.run(action="bogus")


def test_github_tool_search_empty_query():
    tool = GitHubTool()
    assert "Query required" in tool.run(action="search_repos", query="")


def test_github_tool_repo_info_invalid():
    tool = GitHubTool()
    assert "owner/name" in tool.run(action="repo_info", repo="invalid")


def test_youtube_skill_always_configured():
    skill = YouTubeSkill(config={})
    assert skill.is_configured() is True
    tools = skill.tools()
    assert len(tools) == 1
    assert tools[0].name == "youtube"


def test_youtube_tool_search_empty_query():
    tool = YouTubeTool()
    assert "Query required" in tool.run(action="search", query="")


def test_youtube_tool_transcript_no_id():
    tool = YouTubeTool()
    assert "video_id required" in tool.run(action="transcript", video_id="")


def test_youtube_tool_unknown_action():
    tool = YouTubeTool()
    assert "Unknown action" in tool.run(action="bogus")


def test_gmail_skill_unconfigured():
    skill = GmailSkill(config={})
    assert skill.is_configured() is False
    tool = skill.tools()[0]
    result = tool.run(action="inbox")
    assert "not configured" in result.lower()


def test_gmail_skill_configured():
    cfg = {"skills": {"gmail": {"email": "me@x.com", "app_password": "abcd"}}}
    skill = GmailSkill(config=cfg)
    assert skill.is_configured() is True


def test_gmail_tool_unknown_action():
    tool = GmailTool(email_addr="me@x.com", app_password="abc")
    assert "Unknown action" in tool.run(action="bogus")


def test_calendar_skill_unconfigured():
    skill = GoogleCalendarSkill(config={})
    assert skill.is_configured() is False
    tool = skill.tools()[0]
    result = tool.run(action="upcoming")
    assert "not configured" in result.lower()


def test_calendar_parse_ical_basic():
    sample = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Test Event\n"
        "DTSTART:20260501T100000Z\n"
        "END:VEVENT\n"
        "END:VCALENDAR"
    )
    events = GoogleCalendarTool._parse_ical(sample)
    assert len(events) == 1
    assert events[0]["summary"] == "Test Event"


def test_notion_skill_unconfigured():
    skill = NotionSkill(config={})
    assert skill.is_configured() is False


def test_notion_tool_no_token():
    tool = NotionTool(token=None)
    assert "required" in tool.run(action="search", query="x").lower()


def test_notion_tool_unknown_action():
    tool = NotionTool(token="abc")
    assert "Unknown action" in tool.run(action="bogus")


def test_skill_registry_loads_builtins():
    reg = SkillRegistry(config={})
    skills = reg.list_skills()
    names = {s.name for s in skills}
    assert "github" in names
    assert "youtube" in names
    assert "gmail" in names
    assert "calendar" in names
    assert "notion" in names


def test_skill_registry_only_configured_tools():
    reg = SkillRegistry(config={})
    tools = reg.all_tools(only_configured=True)
    tool_names = {t.name for t in tools}
    # github + youtube work without config
    assert "github" in tool_names
    assert "youtube" in tool_names
    # gmail/calendar/notion need config
    assert "gmail" not in tool_names
    assert "calendar" not in tool_names
    assert "notion" not in tool_names


def test_skill_registry_info():
    reg = SkillRegistry(config={})
    info = reg.info()
    assert len(info) == 5
    for entry in info:
        assert "name" in entry
        assert "configured" in entry
        assert "tools" in entry


def test_tool_registry_with_skills():
    from openbro.tools.registry import ToolRegistry

    reg = ToolRegistry(config={})
    # 15 builtins + github + youtube (configured-by-default skills)
    assert "github" in reg.list_tools()
    assert "youtube" in reg.list_tools()
