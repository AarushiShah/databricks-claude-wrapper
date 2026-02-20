# databricks-coding-agent

A CLI wrapper that routes coding agent CLIs — [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Gemini CLI](https://github.com/google-gemini/gemini-cli), and [OpenAI Codex](https://github.com/openai/codex) — through Databricks serving endpoints.

## Quick Start

```bash
# Install
git clone https://github.com/AarushiShah/databricks-claude-wrapper.git
cd databricks-claude-wrapper
pip install .

# Run (Claude via Databricks, the default)
databricks-coding-agent --workspace <your-workspace-url>

# Run Gemini via Databricks
databricks-coding-agent --tool gemini --workspace <your-workspace-url>

# Run Claude in Claude Max mode
databricks-coding-agent --workspace <your-workspace-url> --mode claude_max

# Run Codex via Databricks
databricks-coding-agent --tool codex --workspace <your-workspace-url>
```

The CLI will auto-install the underlying tool (via npm) if it isn't already on your PATH.

## Prerequisites

- Python 3.9+
- Node.js / npm (for auto-installing CLI tools)
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) installed (for OAuth authentication), or a Databricks personal access token

## Installation

```bash
git clone https://github.com/AarushiShah/databricks-claude-wrapper.git
cd databricks-claude-wrapper
pip install .
```

This installs the `databricks-coding-agent` command.

## Authentication

The CLI authenticates to Databricks in one of two ways (checked in order):

1. **Environment variable** — set `DATABRICKS_TOKEN` to a Databricks personal access token.
2. **Databricks CLI OAuth** — if no token is set, the CLI runs `databricks auth token` to get a session token. If no valid session exists, it will automatically launch `databricks auth login` and open a browser for you to authenticate.

## Usage

### Supported tools

| Flag | Tool | Model | npm package |
|---|---|---|---|
| `--tool claude` (default) | Claude Code | `databricks-claude-opus-4-6` | `@anthropic-ai/claude-code` |
| `--tool gemini` | Gemini CLI | `databricks-gemini-3-pro` | `@google/gemini-cli` |
| `--tool codex` | Codex CLI | `databricks-gpt-5-2` | `@openai/codex` |

### Claude (default)

#### Databricks mode (default)

Routes Claude Code directly through a Databricks serving endpoint — no local proxy needed:

```bash
databricks-coding-agent --workspace <your-workspace-url>
```

#### Claude Max mode

Routes through a local proxy that forwards your Anthropic API key alongside Databricks auth:

```bash
databricks-coding-agent --workspace <your-workspace-url> --mode claude_max
```

### Gemini

```bash
databricks-coding-agent --tool gemini --workspace <your-workspace-url>
```

### Codex

```bash
databricks-coding-agent --tool codex --workspace <your-workspace-url>
```

This writes a config to `~/.codex/config.toml` and launches Codex against the Databricks endpoint.

### Passing arguments to the underlying CLI

Any extra arguments after the flags are forwarded to the selected tool:

```bash
databricks-coding-agent --workspace <url> -p "hello"
databricks-coding-agent --tool gemini --workspace <url> -p "hello"
```

### Running without Databricks

If you omit `--workspace`, the CLI simply launches Claude Code with no Databricks routing (passthrough mode):

```bash
databricks-coding-agent
```

## Logs

In `claude_max` mode, proxy logs are written to `~/.databricks-coding-agent/proxy.log`.
