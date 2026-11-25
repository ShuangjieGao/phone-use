"""Run the XFY-phone-use MCP server over newline-delimited UTF-8 stdio."""

import base64
import json
import sys

from . import __version__


VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
OBSERVATION = {"type": "string", "minLength": 1, "description": "ID from the latest phone_observe."}
PIXEL = {"type": "integer", "minimum": 0}


def tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


TOOLS = {item["name"]: item for item in (
    tool("phone_status", "Check the configured Android secondary display.", {}),
    tool("phone_observe", "Read the secondary display and obtain an observation ID. Disable elements to skip OCR when using screenshots.",
         {"screenshot": {"type": "boolean", "default": False},
          "elements": {"type": "boolean", "default": True}}),
    tool("phone_launch", "Open an Android app on the configured secondary display, then observe it.",
         {"package": {"type": "string", "minLength": 1}}, ["package"]),
    tool("phone_tap", "Tap a visible element by ID, or a point in the observed display's pixel coordinates.",
         {"observation_id": OBSERVATION, "element": {"type": "string", "minLength": 1}, "x": PIXEL, "y": PIXEL},
         ["observation_id"]),
    tool("phone_swipe", "Swipe between two points in the observed display's pixel coordinates.",
         {"observation_id": OBSERVATION, "x": PIXEL, "y": PIXEL, "to_x": PIXEL, "to_y": PIXEL},
         ["observation_id", "x", "y", "to_x", "to_y"]),
    tool("phone_type", "Enter printable ASCII text into the focused field on the observed display.",
         {"observation_id": OBSERVATION, "text": {"type": "string", "minLength": 1}},
         ["observation_id", "text"]),
    tool("phone_key", "Press a navigation or editing key on the observed display.",
         {"observation_id": OBSERVATION, "key": {"type": "string", "enum": ["back", "enter", "del"]}},
         ["observation_id", "key"]),
)}


def validate_arguments(name, arguments):
    """Validate the same small schema exposed in tools/list before calling the device."""
    schema = TOOLS[name]["inputSchema"]
    fields = schema["properties"]
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    for key in schema["required"]:
        if key not in arguments:
            raise ValueError(f"Missing argument: {key}")
    for key, value in arguments.items():
        if key not in fields:
            raise ValueError(f"Unknown argument: {key}")
        field = fields[key]
        expected = {"boolean": bool, "string": str, "integer": int}[field["type"]]
        if type(value) is not expected:
            raise ValueError(f"{key} must be a {field['type']}.")
        if "minLength" in field and len(value) < field["minLength"]:
            raise ValueError(f"{key} cannot be empty.")
        if "minimum" in field and value < field["minimum"]:
            raise ValueError(f"{key} must be at least {field['minimum']}.")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"{key} must be one of {', '.join(field['enum'])}.")
    if name == "phone_tap" and (arguments.keys() - {"observation_id"}) not in ({"element"}, {"x", "y"}):
        raise ValueError("Specify either element or both x and y.")


def tool_content(result):
    metadata = dict(result)
    png = metadata.pop("image_png", None)
    image = metadata.pop("screenshot", None)
    content = [{"type": "text", "text": json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))}]
    if png is not None:
        image = {"mimeType": "image/png", "data": base64.b64encode(png).decode("ascii")}
    if image is not None:
        content.append({"type": "image", "mimeType": image["mimeType"], "data": image["data"]})
    return {"content": content}


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Server:
    def __init__(self, device_factory, device_error):
        self.device_factory = device_factory
        self.device_error = device_error
        self.device = None
        self.initialized = False

    def close(self):
        if self.device is not None:
            self.device.close()

    def call(self, name, arguments):
        if not isinstance(name, str) or name not in TOOLS:
            raise RpcError(-32602, "Unknown tool name.")
        try:
            validate_arguments(name, arguments)
            if self.device is None:
                self.device = self.device_factory()
            if name == "phone_status":
                result = self.device.status()
            elif name == "phone_observe":
                result = self.device.observe(**arguments)
            elif name == "phone_launch":
                result = self.device.launch(**arguments)
            else:
                action = {"phone_tap": "tap", "phone_swipe": "swipe", "phone_type": "text", "phone_key": "key"}[name]
                result = self.device.act(action, **arguments)
            return tool_content(result)
        except (self.device_error, ValueError) as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    def dispatch(self, method, params):
        if method == "ping":
            return {}
        if method == "initialize":
            if self.initialized:
                raise RpcError(-32600, "This connection was already initialized.")
            version = params.get("protocolVersion")
            if not isinstance(version, str):
                raise RpcError(-32602, "Missing protocolVersion.")
            self.initialized = True
            return {
                "protocolVersion": version if version in VERSIONS else VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "XFY-phone-use", "version": __version__},
                "instructions": "Use phone_observe before each input. Prefer element IDs and request a screenshot when needed.",
            }
        if not self.initialized:
            raise RpcError(-32002, "Initialize the connection first.")
        if method == "tools/list":
            if params.get("cursor") is not None:
                raise RpcError(-32602, "Unrecognized tools cursor.")
            return {"tools": list(TOOLS.values())}
        if method == "tools/call":
            return self.call(params.get("name"), params.get("arguments", {}))
        raise RpcError(-32601, "Method not found.")

    def handle(self, raw):
        request_id = None
        try:
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                raise RpcError(-32600, "Invalid JSON-RPC request.")
            if "id" not in message:
                return None
            if type(message["id"]) not in (int, str):
                raise RpcError(-32600, "Invalid request ID.")
            request_id = message["id"]
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "Expected params to be an object.")
            result = self.dispatch(message["method"], params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, UnicodeDecodeError):
            error = {"code": -32700, "message": "Invalid UTF-8 JSON."}
        except RpcError as exc:
            error = {"code": exc.code, "message": str(exc)}
        except Exception as exc:
            # Do not include requests, typed text or device output in protocol logs.
            print(f"XFY-phone-use: unexpected {type(exc).__name__}", file=sys.stderr)
            error = {"code": -32603, "message": "Internal server error."}
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def serve(source, destination, server):
    try:
        for line in source:
            response = server.handle(line)
            if response is not None:
                encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                destination.write(encoded + b"\n")
                destination.flush()
    finally:
        server.close()


def main():
    from .device import PhoneDevice, PhoneError
    serve(sys.stdin.buffer, sys.stdout.buffer, Server(PhoneDevice, PhoneError))


if __name__ == "__main__":
    main()
