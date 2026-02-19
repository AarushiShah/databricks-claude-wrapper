#!/usr/bin/env python3
"""
Databricks proxy server for Claude Code.

Authentication (checked in order):
    1. DATABRICKS_TOKEN env var (static PAT)
    2. Databricks CLI OAuth via `databricks auth token` (auto-refreshing)
"""

from flask import Flask, request, jsonify, Response
import requests as http_requests
import subprocess
import os
import json
import logging
import time
import threading

app = Flask(__name__)

# --- Logging setup --------------------------------------------------------

_log_file = None


def _setup_logging(log_path):
    """Route all proxy & Flask/Werkzeug output to a file."""
    global _log_file
    _log_file = log_path

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler.setFormatter(formatter)

    # Proxy logger
    proxy_logger = logging.getLogger("databricks_proxy")
    proxy_logger.setLevel(logging.DEBUG)
    proxy_logger.addHandler(file_handler)
    proxy_logger.propagate = False

    # Silence Werkzeug / Flask console output
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.DEBUG)
    werkzeug_logger.handlers = [file_handler]
    werkzeug_logger.propagate = False

    app.logger.handlers = [file_handler]
    app.logger.setLevel(logging.DEBUG)
    app.logger.propagate = False


log = logging.getLogger("databricks_proxy")

DATABRICKS_HOST = os.environ.get(
    "DATABRICKS_HOST",
    "https://eng-ml-inference.staging.cloud.databricks.com",
)

# --- Token management ---------------------------------------------------

_token_lock = threading.Lock()
_token_cache = {"access_token": None, "expiry": 0}


def get_databricks_token():
    """Return a valid Databricks token.

    Precedence:
      1. DATABRICKS_TOKEN env var  (never expires from our perspective)
      2. Cached CLI OAuth token    (if not within 60 s of expiry)
      3. Fresh CLI OAuth token     (via `databricks auth token`)
    """
    static_token = os.environ.get("DATABRICKS_TOKEN")
    if static_token:
        return static_token

    with _token_lock:
        now = time.time()
        if _token_cache["access_token"] and now < _token_cache["expiry"] - 60:
            return _token_cache["access_token"]

        try:
            result = subprocess.run(
                ["databricks", "auth", "token", "--host", DATABRICKS_HOST],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                token = data.get("access_token")
                if token:
                    expiry_raw = data.get("expiry", 0)
                    if isinstance(expiry_raw, str):
                        from datetime import datetime, timezone
                        try:
                            expiry = datetime.fromisoformat(
                                expiry_raw.replace("Z", "+00:00")
                            ).timestamp()
                        except ValueError:
                            expiry = now + 3600
                    else:
                        expiry = float(expiry_raw) if expiry_raw else now + 3600

                    _token_cache["access_token"] = token
                    _token_cache["expiry"] = expiry
                    return token
        except Exception as e:
            log.warning("Failed to refresh token via CLI: %s", e)

    return None


# --- Routes --------------------------------------------------------------

@app.route('/', methods=['GET'])
def health():
    """Health check endpoint for proxy readiness."""
    return jsonify({"status": "ok"})


@app.route('/v1/messages', methods=['POST'])
def chat_completions():
    """Proxy chat completions to Databricks."""
    try:
        openai_request = request.get_json()
        is_streaming = openai_request.get("stream", False)

        databricks_token = get_databricks_token()
        if not databricks_token:
            return jsonify({"error": {
                "message": "No Databricks token available. Set DATABRICKS_TOKEN or run `databricks auth login`.",
                "type": "config_error",
            }}), 500

        ANTHROPIC_TOKEN = request.headers.get("Authorization", "").replace("Bearer ", "")
        beta_headers = request.headers.get("Anthropic-Beta")

        headers = {
            "Authorization": f"Bearer {databricks_token}",
            "x-anthropic-api-key": ANTHROPIC_TOKEN,
            "anthropic-beta": f"{beta_headers}",
            "x-databricks-traffic-id": "testenv://liteswap/claude_max",
            "Content-Type": "application/json"
        }

        r = http_requests.post(
            f"{DATABRICKS_HOST}/serving-endpoints/anthropic/v1/messages",
            headers=headers,
            json=openai_request,
            stream=is_streaming,
            timeout=180
        )

        if r.status_code != 200:
            log.error("Databricks API error: %s", r.text)
            return jsonify({
                "error": {"message": r.text, "type": "databricks_error", "code": r.status_code}
            }), r.status_code
        else:
            log.info("Databricks API success: %s", r.status_code)

        if is_streaming:
            def generate():
                for chunk in r.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk

            return Response(generate(), mimetype="text/event-stream")
        else:
            return Response(r.content, status=r.status_code,
                            content_type=r.headers.get("Content-Type", "application/json"))

    except http_requests.exceptions.Timeout:
        log.error("Request timed out")
        return jsonify({"error": {"message": "Request timed out", "type": "timeout_error"}}), 504
    except http_requests.exceptions.RequestException as e:
        log.error("Request failed: %s", e)
        return jsonify({"error": {"message": str(e), "type": "connection_error"}}), 500
    except Exception as e:
        log.error("Proxy error: %s", e)
        return jsonify({"error": {"message": str(e), "type": "proxy_error"}}), 500


def run_proxy(host, port=8000, log_path=None):
    """Start the Flask proxy server.

    Args:
        host: Databricks workspace URL to proxy to.
        port: Local port to listen on.
        log_path: File path for proxy logs. If None, logs to ~/.databricks-claude/proxy.log.
    """
    global DATABRICKS_HOST
    DATABRICKS_HOST = host

    if log_path is None:
        log_dir = os.path.expanduser("~/.databricks-claude")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "proxy.log")

    _setup_logging(log_path)
    log.info("Proxy starting on 127.0.0.1:%d -> %s", port, host)
    log.info("Log file: %s", log_path)

    # Use make_server directly instead of app.run() to avoid
    # Werkzeug printing its startup banner to stderr.
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", port, app, threaded=True)
    server.serve_forever()
