#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI entry point for databricks-claude.

Usage:
    databricks-claude                                              # launches claude normally
    databricks-claude --workspace <url> --mode databricks          # direct Databricks endpoint
    databricks-claude --workspace <url> --mode claude_max          # proxy through Databricks
    databricks-claude --workspace <url> --mode databricks -p "hi"  # with claude args
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error


PROXY_PORT = 8000


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


def launch_databricks_mode(workspace, claude_args):
    """Launch Claude Code pointing directly at Databricks' Anthropic endpoint.

    No proxy needed — just sets env vars and execs claude.
    """
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
    ensure_databricks_auth(workspace)

    from databricks_claude.proxy import run_proxy

    os.environ["DATABRICKS_HOST"] = workspace

    log_dir = os.path.expanduser("~/.databricks-claude")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "proxy.log")

    proxy_thread = threading.Thread(
        target=run_proxy, args=(workspace, PROXY_PORT, log_path), daemon=True
    )
    proxy_thread.start()

    print(f"Waiting for proxy on port {PROXY_PORT}...")
    if not wait_for_proxy(PROXY_PORT):
        print("ERROR: Proxy failed to start within 10 seconds.")
        print(f"Check logs at: {log_path}")
        sys.exit(1)
    print(f"Proxy is ready. Logs: {log_path}")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{PROXY_PORT}"

    claude_proc = subprocess.Popen(
        ["claude"] + claude_args,
        env=env,
    )

    def forward_signal(sig, _frame):
        claude_proc.send_signal(sig)

    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGTERM, forward_signal)

    sys.exit(claude_proc.wait())


def main():
    parser = argparse.ArgumentParser(
        prog="databricks-claude",
        description="Launch Claude Code, optionally routed through Databricks.",
    )
    parser.add_argument(
        "--workspace",
        metavar="URL",
        help="Databricks workspace URL (required with --mode).",
    )
    parser.add_argument(
        "--mode",
        choices=["databricks", "claude_max"],
        help="databricks: direct endpoint (no proxy). "
             "claude_max: local proxy that forwards your Anthropic key to Databricks.",
    )

    args, claude_args = parser.parse_known_args()

    # --- No mode/workspace: just exec claude directly ---
    if not args.mode and not args.workspace:
        os.execvp("claude", ["claude"] + claude_args)

    # --- Validate: mode and workspace go together ---
    if args.mode and not args.workspace:
        parser.error("--workspace is required when --mode is specified.")
    if args.workspace and not args.mode:
        parser.error("--mode is required when --workspace is specified.")

    # --- Normalize workspace URL ---
    workspace = args.workspace
    if not workspace.startswith("https://") and not workspace.startswith("http://"):
        workspace = "https://" + workspace
    workspace = workspace.rstrip("/")

    if args.mode == "databricks":
        launch_databricks_mode(workspace, claude_args)
    elif args.mode == "claude_max":
        launch_claude_max_mode(workspace, claude_args)


if __name__ == "__main__":
    main()
