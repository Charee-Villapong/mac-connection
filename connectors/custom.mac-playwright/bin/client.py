#!/usr/bin/env python3
"""MCP client for the Playwright MCP server running on the user's MacBook Air.

The server listens only on the Mac's Tailscale IP (<MacのTailscale IP>:8931).
This VM reaches it through the runtime egress proxy on port 3130 (the
Tailscale tunnel). Proxy credentials are read from HTTPS_PROXY at runtime
and are never stored in this file. There is no other credential: inside the
user's private tailnet, the tunnel admission itself is the gate.
"""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import sys
import urllib.parse

ENDPOINT_HOST = "100.x.x.x"  # 公開版では伏せています。実値に置き換えて使います
ENDPOINT_PORT = 8931
ENDPOINT_PATH = "/mcp"

ALLOWED_TOOLS: set[str] = {
    "browser_close",
    "browser_resize",
    "browser_console_messages",
    "browser_handle_dialog",
    "browser_emulate_media",
    "browser_file_upload",
    "browser_drop",
    "browser_find",
    "browser_fill_form",
    "browser_press_key",
    "browser_type",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_requests",
    "browser_network_request",
    "browser_take_screenshot",
    "browser_snapshot",
    "browser_click",
    "browser_drag",
    "browser_hover",
    "browser_select_option",
    "browser_tabs",
    "browser_wait_for",
}
# Deliberately NOT allowed: browser_evaluate and browser_run_code_unsafe run
# arbitrary code. Add one only after the user agrees to that specific use.

PROTOCOLS = {"2025-11-25", "2025-06-18", "2025-03-26"}


class McpError(RuntimeError):
    """A transport or protocol failure with no automatic replay."""


def proxy_target():
    raw = os.environ.get("HTTPS_PROXY")
    if not raw:
        raise McpError("HTTPS_PROXY is not set; the Tailscale tunnel is unavailable")
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.hostname or not parsed.username or not parsed.password:
        raise McpError("HTTPS_PROXY is not in the expected form")
    # The port alone selects the Tailscale tunnel: replace it with 3130.
    return parsed.hostname, 3130, parsed.username, parsed.password


class Client:
    def __init__(self):
        self.session = None
        self.protocol = "2025-03-26"
        self.sequence = 0

    def post(self, body: dict):
        proxy_host, proxy_port, proxy_user, proxy_pass = proxy_target()
        connection = http.client.HTTPConnection(proxy_host, proxy_port, timeout=60)
        token = base64.b64encode(f"{proxy_user}:{proxy_pass}".encode()).decode()
        connection.set_tunnel(
            ENDPOINT_HOST,
            ENDPOINT_PORT,
            headers={"Proxy-Authorization": f"Basic {token}"},
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        try:
            connection.request("POST", ENDPOINT_PATH, json.dumps(body), headers)
            response = connection.getresponse()
            payload = response.read()
            if session := response.getheader("Mcp-Session-Id"):
                if not all(33 <= ord(c) <= 126 for c in session):
                    raise McpError("MCP server returned an invalid session header")
                self.session = session
            if response.status >= 400:
                raise McpError(
                    f"MCP returned HTTP {response.status}; the operation was not replayed"
                )
            return response.status, response.getheader("Content-Type", ""), payload
        except (OSError, http.client.HTTPException) as error:
            raise McpError(
                "MCP request did not complete; check any write before retrying"
            ) from error
        finally:
            connection.close()

    def send(self, method, params, *, notification=False):
        self.sequence += 1
        request_id = None if notification else self.sequence
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            body["id"] = request_id
        status, _content_type, payload = self.post(body)
        if notification:
            if status in (200, 202):
                return None
            raise McpError("MCP server did not accept the initialization notification")
        return self.read_result(payload, request_id)

    @staticmethod
    def decode(payload: bytes, request_id):
        message = json.loads(payload)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise McpError("MCP returned an invalid JSON-RPC message")
        if type(message.get("id")) is not int or message["id"] != request_id:
            return None
        if "error" in message:
            raise McpError("MCP returned a JSON-RPC error; the operation was not replayed")
        if not isinstance(message.get("result"), dict):
            raise McpError("MCP returned no result object")
        return message["result"]

    @classmethod
    def read_result(cls, payload: bytes, request_id):
        text = payload.decode("utf-8", errors="replace").strip()
        if text.startswith("{"):
            result = cls.decode(text.encode(), request_id)
            if result is None:
                raise McpError("MCP returned the wrong request ID")
            return result
        for line in text.splitlines():
            if line.startswith("data:"):
                result = cls.decode(line[5:].strip().encode(), request_id)
                if result is not None:
                    return result
        raise McpError("MCP event stream ended before the matching result")

    def initialize(self):
        result = self.send(
            "initialize",
            {
                "protocolVersion": self.protocol,
                "capabilities": {},
                "clientInfo": {"name": "muse-custom-connector", "version": "1"},
            },
        )
        protocol = result.get("protocolVersion")
        capabilities = result.get("capabilities")
        if (
            not isinstance(protocol, str)
            or protocol not in PROTOCOLS
            or not isinstance(capabilities, dict)
            or not isinstance(capabilities.get("tools"), dict)
        ):
            raise McpError("MCP server did not negotiate a supported tool protocol")
        self.protocol = protocol
        self.send("notifications/initialized", {}, notification=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    listing = subparsers.add_parser("list-tools")
    listing.add_argument("--cursor")
    calling = subparsers.add_parser("call-tool")
    calling.add_argument("--name", required=True)
    calling.add_argument("--arguments-file", type=Path, required=True)
    batch = subparsers.add_parser("run-batch")
    batch.add_argument("--calls-file", type=Path, required=True)
    args = parser.parse_args()

    def checked(name, arguments):
        if name not in ALLOWED_TOOLS:
            parser.error(
                "Inspect this operation's schema and add its exact name to ALLOWED_TOOLS"
            )
        if not isinstance(arguments, dict):
            parser.error("arguments must be a JSON object")

    calls = []
    if args.command == "call-tool":
        arguments = json.loads(args.arguments_file.read_bytes())
        checked(args.name, arguments)
        calls = [{"name": args.name, "arguments": arguments}]
    elif args.command == "run-batch":
        calls = json.loads(args.calls_file.read_bytes())
        if not isinstance(calls, list) or not calls:
            parser.error("calls file must be a non-empty JSON array")
        for call in calls:
            if not isinstance(call, dict) or "name" not in call:
                parser.error("each call must be an object with a name")
            checked(call["name"], call.get("arguments", {}))

    client = Client()
    client.initialize()
    if args.command == "list-tools":
        result = client.send("tools/list", {"cursor": args.cursor} if args.cursor else {})
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("isError") else 0

    results = []
    exit_code = 0
    try:
        for call in calls:
            result = client.send(
                "tools/call",
                {"name": call["name"], "arguments": call.get("arguments", {})},
            )
            results.append({"name": call["name"], "result": result})
            if result.get("isError"):
                exit_code = 1
                break
    finally:
        # A session that ends without browser_close orphans the Chromium
        # process, which then holds the profile lock and blocks every later
        # session ("Browser is already in use"). Always close in-session.
        if calls and calls[-1]["name"] != "browser_close":
            try:
                client.send(
                    "tools/call", {"name": "browser_close", "arguments": {}}
                )
            except McpError:
                pass
    print(json.dumps(results if len(results) > 1 else results[0]["result"], ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (McpError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
