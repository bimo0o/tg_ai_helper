# Project rules

- `app/` contains the bot; `app/handlers/` contains Telegram adapters only.
- `app/mcp/` owns protocol integration; domain services must not depend on Telegram.
- `tests/` contains deterministic unit tests and opt-in PostgreSQL integration tests.
- `docs/` contains product operations and lab material; keep the root README concise.
- Use Python 3.12 from `.venv`. Check with `python -m pytest -q`, `python -m ruff check .`,
  `python -m ruff format --check .`, and `python -m pip check`.
- Integration checks use `RUN_INTEGRATION=1 python -m pytest -m integration -v` and an isolated Docker database.
- Apply additive, versioned migrations in `app/schema.py`. Never delete live data to repair a schema.
- New environment variables need validation and safe examples in `.env.example`.
- Never read real `.env`, expose credentials, log dialogue/profile contents or user IDs, or commit personal schedules.
- Never call live Telegram/LLM/weather APIs in automated tests. Do not run paid experiments as the agent.
- Handlers do not execute SQL or HTTP requests. Model outputs and MCP results are untrusted data.
- Owners come from Telegram updates, never from model arguments. Writes require a valid confirmed action.
- Preserve lab 1 behavior, history boundaries, profile isolation and Telegram formatting.
- Update `spec.md`, `plan.md` and relevant documentation with implementation changes.
- Report tests by scenario and actual outcome, distinguishing stubs, real PostgreSQL and live-model checks.
- Make focused commits. Never claim an unavailable manual check or experiment has passed.
