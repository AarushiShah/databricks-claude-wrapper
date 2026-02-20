#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI entry point for databricks-coding-agent.

Usage:
    databricks-coding-agent                                                    # launches claude normally
    databricks-coding-agent --workspace <url>                                  # Claude via Databricks (default)
    databricks-coding-agent --workspace <url> --mode claude_max                # Claude proxy mode
    databricks-coding-agent --tool gemini --workspace <url>                    # Gemini via Databricks
    databricks-coding-agent --tool codex --workspace <url>                     # Codex via Databricks
    databricks-coding-agent --tool claude --workspace <url> -p "hi"            # with tool-specific args
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error


PROXY_PORT = 8000

# Map CLI command names to their npm package names
NPM_PACKAGES = {
    "claude": "@anthropic-ai/claude-code",
    "gemini": "@google/gemini-cli",
    "codex": "@openai/codex",
}


def ensure_cli_installed(command):
    """Check if a CLI tool is on PATH; if not, install it via npm.

    Args:
        command: The CLI command name (e.g. "gemini", "codex").
    """
    if shutil.which(command):
        return

    package = NPM_PACKAGES.get(command)
    if not package:
        print(f"ERROR: '{command}' is not installed and no npm package is known for it.")
        sys.exit(1)

    if not shutil.which("npm"):
        print(f"ERROR: '{command}' is not installed and npm is not available to install it.")
        print("Please install Node.js/npm first, then re-run.")
        sys.exit(1)

    print(f"'{command}' not found. Installing {package} via npm...")
    try:
        result = subprocess.run(
            ["npm", "install", "-g", package],
            timeout=120,
        )
        if result.returncode != 0:
            print(f"ERROR: Failed to install {package}.")
            sys.exit(1)
        print(f"Successfully installed {package}.\n")
    except subprocess.TimeoutExpired:
        print(f"ERROR: npm install timed out for {package}.")
        sys.exit(1)


def ensure_databricks_auth(host):
    """Ensure Databricks CLI auth is configured for the given host.

    If DATABRICKS_TOKEN is already set, skip entirely.
    Otherwise, try `databricks auth token` and fall back to
    `databricks auth login` if no valid session exists.
    """
    if os.environ.get("DATABRICKS_TOKEN"):
        print("Using DATABRICKS_TOKEN from environment.")
        return

    print(f"No DATABRICKS_TOKEN set. Checking Databricks CLI auth for {host}...")

    try:
        result = subprocess.run(
            ["databricks", "auth", "token", "--host", host],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("access_token"):
                print("Found valid Databricks CLI session.")
                return
    except FileNotFoundError:
        print("ERROR: 'databricks' CLI not found. Install it or set DATABRICKS_TOKEN.")
        sys.exit(1)
    except Exception:
        pass

    print(f"\nNo valid session found. Launching `databricks auth login`...")
    print("A browser window will open for authentication.\n")
    try:
        login_result = subprocess.run(
            ["databricks", "auth", "login", "--host", host],
            timeout=120,
        )
        if login_result.returncode != 0:
            print("ERROR: `databricks auth login` failed.")
            sys.exit(1)
        print("Authentication successful!\n")
    except subprocess.TimeoutExpired:
        print("ERROR: Authentication timed out.")
        sys.exit(1)


def get_databricks_token(host):
    """Get a Databricks token for the given host.

    Checks DATABRICKS_TOKEN env var first, then falls back to CLI OAuth.
    """
    static_token = os.environ.get("DATABRICKS_TOKEN")
    if static_token:
        return static_token

    try:
        result = subprocess.run(
            ["databricks", "auth", "token", "--host", host],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            token = data.get("access_token")
            if token:
                return token
    except Exception:
        pass

    return None


def wait_for_proxy(port, timeout=10):
    """Poll localhost until the proxy is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return True
        except urllib.error.URLError:
            pass
        except Exception:
            pass
        time.sleep(0.2)
    return False


def find_proxy_port(workspace):
    """Find a port for the proxy, reusing an existing one if compatible.

    Returns (port, already_running):
        - If a proxy for the same workspace is already on PROXY_PORT, reuse it.
        - If PROXY_PORT is free, claim it.
        - Otherwise, let the OS pick a free port.
    """
    # Check if our proxy is already running on the default port
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{PROXY_PORT}/", timeout=2
        )
        data = json.loads(resp.read())
        if data.get("status") == "ok" and data.get("workspace") == workspace:
            return PROXY_PORT, True
    except Exception:
        pass

    # Check if the default port is available
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", PROXY_PORT))
        s.close()
        return PROXY_PORT, False
    except OSError:
        pass

    # Default port taken by something else — pick a free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port, False


def launch_databricks_mode(workspace, claude_args):
    """Launch Claude Code pointing directly at Databricks' Anthropic endpoint.

    No proxy needed — just sets env vars and execs claude.
    """
    ensure_cli_installed("claude")
    ensure_databricks_auth(workspace)

    token = get_databricks_token(workspace)
    if not token:
        print("ERROR: Could not obtain Databricks token.")
        sys.exit(1)

    os.environ["ANTHROPIC_MODEL"] = "databricks-claude-opus-4-6"
    os.environ["ANTHROPIC_BASE_URL"] = f"{workspace}/serving-endpoints/anthropic"
    os.environ["ANTHROPIC_AUTH_TOKEN"] = token
    os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "x-databricks-use-coding-agent-mode: true"
    os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"

    print(f"Launching Claude Code in Databricks mode")
    print(f"  Model:    databricks-claude-opus-4-6")
    print(f"  Endpoint: {workspace}/anthropic")

    # exec replaces this process — no proxy needed
    os.execvp("claude", ["claude"] + claude_args)


def launch_claude_max_mode(workspace, claude_args):
    """Launch Claude Code with a local proxy that routes through Databricks.

    The proxy intercepts Claude's Anthropic API key and forwards it to
    Databricks as x-anthropic-api-key alongside Databricks auth.
    """
    ensure_cli_installed("claude")
    ensure_databricks_auth(workspace)

    os.environ["DATABRICKS_HOST"] = workspace

    port, already_running = find_proxy_port(workspace)

    if already_running:
        print(f"Reusing existing proxy on port {port}.")
    else:
        from databricks_coding_agent.proxy import run_proxy

        log_dir = os.path.expanduser("~/.databricks-coding-agent")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "proxy.log")

        proxy_thread = threading.Thread(
            target=run_proxy, args=(workspace, port, log_path), daemon=True
        )
        proxy_thread.start()

        print(f"Waiting for proxy on port {port}...")
        if not wait_for_proxy(port):
            print("ERROR: Proxy failed to start within 10 seconds.")
            print(f"Check logs at: {log_path}")
            sys.exit(1)
        print(f"Proxy is ready. Logs: {log_path}")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"

    claude_proc = subprocess.Popen(
        ["claude"] + claude_args,
        env=env,
    )

    def forward_signal(sig, _frame):
        claude_proc.send_signal(sig)

    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGTERM, forward_signal)

    sys.exit(claude_proc.wait())


def launch_gemini_mode(workspace, tool_args):
    """Launch Gemini CLI routed through a Databricks serving endpoint."""
    ensure_cli_installed("gemini")
    ensure_databricks_auth(workspace)

    token = get_databricks_token(workspace)
    if not token:
        print("ERROR: Could not obtain Databricks token.")
        sys.exit(1)

    os.environ["GEMINI_MODEL"] = "databricks-gemini-3-pro"
    os.environ["GOOGLE_GEMINI_BASE_URL"] = f"{workspace}/serving-endpoints/gemini"
    os.environ["GEMINI_API_KEY_AUTH_MECHANISM"] = "bearer"
    os.environ["GEMINI_API_KEY"] = token

    print(f"Launching Gemini CLI in Databricks mode")
    print(f"  Model:    databricks-gemini-3-pro")
    print(f"  Endpoint: {workspace}/serving-endpoints/gemini")

    os.execvp("gemini", ["gemini"] + tool_args)


def launch_codex_mode(workspace, tool_args):
    """Launch Codex CLI routed through a Databricks serving endpoint."""
    ensure_cli_installed("codex")
    ensure_databricks_auth(workspace)

    token = get_databricks_token(workspace)
    if not token:
        print("ERROR: Could not obtain Databricks token.")
        sys.exit(1)

    os.environ["DATABRICKS_TOKEN"] = token

    # Write ~/.codex/config.toml
    codex_dir = os.path.expanduser("~/.codex")
    os.makedirs(codex_dir, exist_ok=True)
    config_path = os.path.join(codex_dir, "config.toml")

    config_content = f"""\
profile = "default"
web_search = "disabled"

[profiles.default]
model_provider = "proxy"
model = "databricks-gpt-5-2"

[model_providers.proxy]
name = "Databricks Proxy"
base_url = "{workspace}/serving-endpoints"
env_key = "DATABRICKS_TOKEN"
wire_api = "responses"
"""

    with open(config_path, "w") as f:
        f.write(config_content)

    print(f"Launching Codex CLI in Databricks mode")
    print(f"  Model:    databricks-gpt-5-2")
    print(f"  Endpoint: {workspace}/serving-endpoints")
    print(f"  Config:   {config_path}")

    os.execvp("codex", ["codex"] + tool_args)


def main():
    parser = argparse.ArgumentParser(
        prog="databricks-coding-agent",
        description="Launch coding agent CLIs (Claude, Gemini, Codex) routed through Databricks.",
    )
    parser.add_argument(
        "--tool",
        choices=["claude", "gemini", "codex"],
        default="claude",
        help="Which coding agent CLI to launch (default: claude).",
    )
    parser.add_argument(
        "--workspace",
        metavar="URL",
        help="Databricks workspace URL (required for all tools).",
    )
    parser.add_argument(
        "--mode",
        choices=["databricks", "claude_max"],
        help="Claude-specific mode. databricks: direct endpoint (default). "
             "claude_max: local proxy that forwards your Anthropic key to Databricks.",
    )

    args, tool_args = parser.parse_known_args()

    # --- No flags at all: just exec claude directly (backward compat) ---
    if not args.workspace and not args.mode:
        ensure_cli_installed("claude")
        os.execvp("claude", ["claude"] + tool_args)

    # --- Validate: workspace is required for all tools ---
    if not args.workspace:
        parser.error("--workspace is required.")

    # --- Validate: --mode is only relevant for Claude ---
    if args.mode and args.tool != "claude":
        parser.error("--mode is only supported with --tool claude.")

    # --- Normalize workspace URL ---
    workspace = args.workspace
    if not workspace.startswith("https://") and not workspace.startswith("http://"):
        workspace = "https://" + workspace
    workspace = workspace.rstrip("/")

    # --- Dispatch ---
    if args.tool == "claude":
        mode = args.mode or "databricks"
        if mode == "databricks":
            launch_databricks_mode(workspace, tool_args)
        elif mode == "claude_max":
            launch_claude_max_mode(workspace, tool_args)
    elif args.tool == "gemini":
        launch_gemini_mode(workspace, tool_args)
    elif args.tool == "codex":
        launch_codex_mode(workspace, tool_args)


if __name__ == "__main__":
    main()
