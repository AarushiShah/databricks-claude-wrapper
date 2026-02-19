# databricks-claude

A CLI wrapper that routes [Claude Code](https://docs.anthropic.com/en/docs/claude-code) through Databricks serving endpoints.

## Prerequisites

- Python 3.9+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and available on your PATH
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) installed (for OAuth authentication), or a Databricks personal access token

## Installation

Clone the repository and install with pip:

```bash
git clone https://github.com/AarushiShah/databricks-claude-wrapper.git
cd databricks-claude-wrapper
pip install .
```

This installs the `databricks-claude` command.

## Authentication

The CLI authenticates to Databricks in one of two ways (checked in order):

1. **Environment variable** — set `DATABRICKS_TOKEN` to a Databricks personal access token.
2. **Databricks CLI OAuth** — if no token is set, the CLI runs `databricks auth token` to get a session token. If no valid session exists, it will automatically launch `databricks auth login` and open a browser for you to authenticate.

## Usage

### Databricks mode

Route Claude Code directly through a Databricks serving endpoint (no local proxy):

```bash
databricks-claude --workspace <your-workspace-url> --mode databricks
```

### Claude Max mode

Route through a local proxy that forwards your Anthropic API key alongside Databricks auth:

```bash
databricks-claude --workspace <your-workspace-url> --mode claude_max
```

### Passing arguments to Claude Code

Any extra arguments are forwarded to the underlying `claude` CLI:

```bash
databricks-claude --workspace <your-workspace-url> --mode databricks -p "hello"
```

## Logs

In `claude_max` mode, proxy logs are written to `~/.databricks-claude/proxy.log`.