"""Check an installed phone-use server over stdio without sending device input."""

import argparse
import json
import re
import subprocess
import sys
import threading


REQUIRED_TOOLS = {"phone_status", "phone_launch", "phone_observe", "phone_tap", "phone_swipe",
                  "phone_type", "phone_key"}
DISPLAY_REASONS = {
    "Primary foreground could not be verified": "primary foreground unverified",
    "Primary foreground changed; stopped secondary control": "primary foreground changed",
    "Secondary display runtime unavailable or malformed": "display runtime unavailable",
    "No active secondary display socket": "display runtime socket unavailable",
    "Selected display is no longer a scrcpy display": "secondary display unavailable",
    "Secondary window disappeared": "secondary display unavailable",
}


def check(child):
    def send(message):
        child.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        child.stdin.flush()

    def request(number, method, params):
        send({"id": number, "method": method, "params": params})
        for line in child.stdout:
            message = json.loads(line)
            if message.get("id") == number:
                if "error" in message:
                    raise ValueError(f"{method} failed")
                return message["result"]
        raise ValueError("Server closed its output")

    def call(number, name, arguments):
        result = request(number, "tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ValueError(f"{name} failed")
        return json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))

    hello = request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "phone-use-check", "version": "1"}})
    info = hello["serverInfo"]
    if info["name"] != "phone-use" or not re.fullmatch(r"\d+(?:\.\d+){2}", info["version"]):
        raise ValueError("Expected the released phone-use server")
    print(f"Server   {info['name']} {info['version']}")
    send({"method": "notifications/initialized"})
    tools = request(2, "tools/list", {})["tools"]
    if not REQUIRED_TOOLS.issubset(item["name"] for item in tools):
        raise ValueError("Expected phone tools are missing")
    print(f"Tools    {len(tools)} available")
    state = call(3, "phone_status", {})
    if state.get("ready") is not True:
        print("Display  not ready")
        print("Reason   " + DISPLAY_REASONS.get(state.get("reason"), "display check unavailable"))
        return 1
    width, height = int(state["width"]), int(state["height"])
    print(f"Display  ready, {width} x {height} px")
    frame = call(4, "phone_observe", {"elements": False, "screenshot": False})
    if (frame.get("source"), frame.get("width"), frame.get("height")) != ("frame", width, height):
        raise ValueError("Display changed or frame unavailable")
    print("Frame    received (OCR off, screenshot off)")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="phone-use", help="Path to the installed MCP executable")
    args = parser.parse_args()
    try:
        with subprocess.Popen([args.server], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, encoding="utf-8") as child:
            timeout = threading.Timer(45, child.kill)
            timeout.start()
            try:
                code = check(child)
                child.stdin.close()
                return code if child.wait() == 0 else 1
            finally:
                timeout.cancel()
                if child.poll() is None:
                    child.kill()
    except (OSError, KeyError, ValueError, TypeError, StopIteration):
        print("Check failed. Verify the server path and display runtime.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
