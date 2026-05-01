# Contributing to OpenBro

Bhai, thanks for wanting to contribute! OpenBro is open source and built for the community.

## Quick Start for Contributors

```bash
git clone https://github.com/brijeshch8482/openbro.git
cd openbro
pip install -e ".[dev,all]"
pytest
```

## Before You Open a PR

1. **Lint + format check** (must pass — CI enforces this):
   ```bash
   ruff check openbro/ tests/
   ruff format --check openbro/ tests/
   ```
2. **Tests**:
   ```bash
   pytest tests/
   ```
3. Keep PRs focused. One feature / fix per PR.
4. New tools/skills → please include tests.

## Project Layout

| Folder | What lives there |
|--------|------------------|
| `openbro/core/` | Agent brain — orchestrates LLM + tools + memory |
| `openbro/llm/` | LLM provider adapters (Ollama, Claude, OpenAI, Groq) |
| `openbro/tools/` | Built-in tools — read [tools/base.py](openbro/tools/base.py) for the interface |
| `openbro/skills/` | Plugin system — see [skills/base.py](openbro/skills/base.py) |
| `openbro/channels/` | I/O channels (CLI, Telegram, ...) |
| `openbro/memory/` | SQLite store + MemoryManager (working/session/long-term) |
| `openbro/voice/` | STT + TTS + wake-word listener |
| `openbro/cli/` | Click entry, REPL, wizard, voice mode |

## Adding a New Tool

1. Create `openbro/tools/my_tool.py` with a class extending `BaseTool`.
2. Set `name`, `description`, `risk` (SAFE / MODERATE / DANGEROUS).
3. Implement `schema()` (JSON Schema) and `run(**kwargs) -> str`.
4. Register it in `openbro/tools/registry.py` `BUILTIN_TOOLS`.
5. Bump the count in `tests/test_tools.py` and add a small test.

## Adding a New Skill (plugin)

A Skill is a bundle of related tools + config. See `openbro/skills/builtin/github.py` as a template.

1. Subclass `BaseSkill`. Set `name`, `description`, `version`, `config_keys`.
2. Implement `tools()` returning a list of `BaseTool` instances.
3. Either drop it as a built-in (in `openbro/skills/builtin/`) or as a user skill at `~/.openbro/skills/<name>/skill.py`.

## Adding an LLM Provider

1. Create `openbro/llm/<name>.py` extending `LLMProvider`.
2. Implement `chat()`, `stream()`, `name()`, `supports_tools()`.
3. Wire it into `openbro/llm/router.py::create_provider`.
4. Add an extras entry in `pyproject.toml`.

## Code Style

- Python 3.10+ syntax (use `X | None`, `list[str]`, etc.)
- Line length: 100
- Imports sorted (ruff `I` rules)
- No unused imports, no f-strings without placeholders

## Reporting Bugs

Open an issue with:
- OS + Python version
- Steps to reproduce
- Expected vs actual behaviour
- (If relevant) anonymised `~/.openbro/audit.log` excerpt

## Code of Conduct

Be a bro. Be respectful, helpful, and patient. We're building this for everyone.

## License

By contributing you agree your work will be licensed under the [MIT License](LICENSE).
