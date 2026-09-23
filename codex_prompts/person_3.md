# Codex prompt for Person 3

Read `README.md`, `AGENTS.md`, and `docs/architecture.md`. You are Person 3 and own integration. Work in `src/wind_forecast/agent/`, `app.py`, `scripts/`, shared contracts, root packaging, and README. Keep the mock path runnable without network or API keys. Commit on `codex/person-3-agent` in your own clone.

The starter has a runnable deterministic workflow. Extend it into a bounded OpenAI Responses API function-calling agent with allowlisted local tools for weather retrieval, input audit, numerical prediction, forecast inspection and saving. Use the model configured in `OPENAI_MODEL`; do not assume access to a particular model. Python remains responsible for issue-time/provenance enforcement, tool argument validation, bounded retries, forecast completeness, and output persistence. The LLM may analyze errors and select safe recovery steps; it must not produce numerical power rows. Keep an offline fallback.

Finish the Streamlit fixture view and add selection of fixture/historical mode, issue time and turbine, provenance display, run trace, output download, and versioned recalculation when a new eligible bundle arrives. Never present fixture data as real. Document exact commands and any required keys. Coordinate shared-contract changes and integration with Persons 1 and 2 instead of rewriting their modules.
