# Arvis MCP Context Servant

[← Documentation index](README.md) · [Project README](../README.md)

## Universal MCP Server Behavior

Arvis MCP Context Servant exists so MCP-compatible coding agents can get compact project facts without spending large amounts of context on first-pass exploration. It is a fact servant, not the main programmer. The coding agent remains responsible for deciding what to change, verifying files directly, editing code, and running tests.

The normal Arvis REPL/chat process does not need to be online. Any MCP-compatible coding agent can start `arvis_mcp_server.py` as a separate stdio MCP server:

```bash
.venv/bin/python arvis_mcp_server.py
```

The public repo implementation is universal and agent-neutral. It does not depend on Codex, local chat state, Ollama, network calls, personal paths, local tokens, private model names, or machine-specific workflow assumptions.

Available tools:

- `project_map` - bounded map of safe text files, sizes, file kinds, and extension counts.
- `grep_project` - bounded search over safe text files.
- `read_file_excerpt` - bounded line excerpt from one safe text file.
- `git_status_summary` - bounded output from fixed safe git status/diff commands.
- `task_brief` - compact grep-based hints for a task.
- `memory_read` - read bounded local project memory from `.arvis_mcp_memory/`.
- `memory_append` - append a bounded note to allowed memory files under `.arvis_mcp_memory/`.

Safety boundaries:

- MCP tools must not edit source code.
- MCP tools must not run arbitrary shell commands.
- Git inspection uses a fixed command allowlist and `shell=False`.
- The server does not make network calls.
- Paths returned to clients are relative paths only.
- Outputs are bounded for token-friendly use.
- Private/generated folders and secret files are excluded, including `.env`, `.env.local`, `.runtime`, `.cache`, `.venv`, `venv`, `node_modules`, `.git`, `models`, `ollama-models`, `logs`, `.codex`, and `.arvis_mcp_memory`.
- MCP memory is hints only. Verify project files directly before editing.
- The only write operation is appending bounded notes to `.arvis_mcp_memory/`.

Project root selection:

1. Tool argument `project_root`, if provided.
2. `ARVIS_MCP_PROJECT_ROOT`, if set.
3. Current working directory.

## Example: Using It With Codex

Codex is one supported MCP client example. Exact Codex config paths and field support may differ by local Codex version. Keep real local config outside the public repo.

Example Codex MCP config:

```toml
[mcp_servers.arvis_context]
command = ".venv/bin/python"
args = ["arvis_mcp_server.py"]
cwd = "/absolute/path/to/arvis_app"
startup_timeout_sec = 20
tool_timeout_sec = 60
enabled = true

[mcp_servers.arvis_context.env]
ARVIS_MCP_PROJECT_ROOT = "/absolute/path/to/arvis_app"
```

CLI-style Codex example:

```bash
codex mcp add arvis_context \
  --env ARVIS_MCP_PROJECT_ROOT=/absolute/path/to/arvis_app \
  -- .venv/bin/python arvis_mcp_server.py
```

If your Codex TUI supports `/mcp`, use it to verify that the server is registered and tools are visible. If MCP does not start, run this manually from the repository root to see the Python error:

```bash
.venv/bin/python arvis_mcp_server.py
```

## Local/Private Configuration That Must Not Be Committed

Personal MCP client configuration must stay outside the public repo or inside ignored local files. Do not commit real local Codex config, private paths, usernames, tokens, secrets, local memory contents, or machine-specific settings.

Safe local places include:

- `.env`
- `.env.local`
- `.codex/config.toml` when project-local and ignored
- user-level MCP client config outside this repository
- `.arvis_mcp_memory/`

The public docs should use placeholders such as `/absolute/path/to/arvis_app`, never real local paths.
