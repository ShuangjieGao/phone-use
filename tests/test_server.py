import base64
import io
import json
import unittest
from unittest.mock import Mock

from xfy_phone_use import __version__
from xfy_phone_use.server import Server, serve


class DeviceError(Exception):
    pass


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.device = Mock(spec=["status", "observe", "launch", "act", "close"])
        self.factory = Mock(return_value=self.device)
        self.server = Server(self.factory, DeviceError)
        self.request("initialize", {"protocolVersion": "2025-06-18"})

    def request(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": "xfy-1", "method": method, "params": params or {}}
        return self.server.handle(json.dumps(payload).encode("utf-8"))

    def call(self, name, **args):
        return self.request("tools/call", {"name": name, "arguments": args})["result"]

    def test_handshake_and_discovery_without_phone(self):
        self.assertEqual(self.request("ping")["result"], {})
        tools = self.request("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {
            "phone_status", "phone_observe", "phone_launch", "phone_tap", "phone_swipe", "phone_type", "phone_key",
        })
        self.factory.assert_not_called()
        new = Server(self.factory, DeviceError)
        response = new.dispatch("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(response["protocolVersion"], "2025-03-26")
        self.assertEqual(response["serverInfo"], {"name": "phone-use", "version": __version__})

    def test_observe_sends_image_once(self):
        raw = b"image-data-for-test"
        self.device.observe.return_value = {"observation_id": "a", "image_png": raw}
        content = self.call("phone_observe", screenshot=True)["content"]
        self.assertEqual(json.loads(content[0]["text"]), {"observation_id": "a"})
        self.assertEqual(content[1], {"type": "image", "mimeType": "image/png", "data": base64.b64encode(raw).decode()})
        self.device.observe.assert_called_once_with(screenshot=True)

    def test_can_disable_ocr(self):
        self.device.observe.return_value = {"source": "frame", "elements": []}
        response = self.call("phone_observe", elements=False)
        self.device.observe.assert_called_once_with(elements=False)
        self.assertEqual(json.loads(response["content"][0]["text"])["source"], "frame")

    def test_since_passes_through_and_an_unchanged_result_has_only_metadata(self):
        self.device.observe.return_value = {"observation_id": "b", "unchanged": True}
        response = self.call("phone_observe", screenshot=True, since="a")
        self.device.observe.assert_called_once_with(screenshot=True, since="a")
        self.assertEqual(len(response["content"]), 1)
        self.assertEqual(json.loads(response["content"][0]["text"]),
                         {"observation_id": "b", "unchanged": True})

    def test_sensitive_bound_editor_is_not_returned_and_unicode_is_forwarded(self):
        self.device.observe.return_value = {"observation_id": "a", "editor": {"status": "ready"}}
        self.call("phone_observe", elements=False, editor=True)
        self.device.observe.assert_called_once_with(elements=False, editor=True)
        self.device.act.return_value = {"action": "text", "submitted": True, "effect": "verified", "readback": True}
        result = self.call("phone_type", observation_id="a", text="买种子")
        self.device.act.assert_called_once_with("text", observation_id="a", text="买种子")
        self.assertEqual(json.loads(result["content"][0]["text"])["effect"], "verified")

    def test_ambiguous_input_is_a_failed_mcp_result_and_never_reports_success(self):
        error = DeviceError("Verify the editor before retry.")
        error.receipt = {"effect": "unconfirmed", "action": "text", "retry_safe": False}
        self.device.act.side_effect = error
        result = self.call("phone_type", observation_id="a", text="中文")
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), error.receipt)

    def test_all_actions_route_to_observed_display(self):
        self.device.act.return_value = {"sent": True}
        examples = [
            ("phone_tap", "tap", {"element": "e1"}),
            ("phone_tap", "tap", {"x": 3, "y": 2}),
            ("phone_swipe", "swipe", {"x": 0, "y": 10, "to_x": 0, "to_y": 0}),
            ("phone_type", "text", {"text": "hello world"}),
            ("phone_key", "key", {"key": "back"}),
        ]
        for name, action, args in examples:
            with self.subTest(name=name, args=args):
                self.call(name, observation_id="observed", **args)
                self.device.act.assert_called_with(action, observation_id="observed", **args)
        self.device.launch.return_value = {"opened": True}
        self.call("phone_launch", package="com.example.app")
        self.device.launch.assert_called_once_with(package="com.example.app")
        self.device.status.return_value = {"display_id": 2}
        self.assertEqual(json.loads(self.call("phone_status")["content"][0]["text"]), {"display_id": 2})

    def test_bad_arguments_do_not_reach_device(self):
        cases = [
            ("phone_tap", {"x": 0, "y": 2}),
            ("phone_tap", {"observation_id": "id", "element": "e1", "x": 0}),
            ("phone_tap", {"observation_id": "id", "x": True, "y": 1}),
            ("phone_swipe", {"observation_id": "id", "x": -1, "y": 0, "to_x": 4, "to_y": 5}),
            ("phone_key", {"observation_id": "id", "key": "home"}),
            ("phone_status", {"display_id": 0}),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                self.assertTrue(self.call(name, **args)["isError"])
        self.factory.assert_not_called()

    def test_device_errors_are_visible_tool_results(self):
        self.device.act.side_effect = DeviceError("Stale observation: observe again.")
        result = self.call("phone_key", observation_id="old", key="back")
        self.assertTrue(result["isError"])
        self.assertIn("observe again", result["content"][0]["text"])

    def test_stdio_recovery_notifications_and_close(self):
        self.device.status.return_value = {"name": "小肥鱼"}
        source = io.BytesIO(
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'broken json\n'
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"phone_status"}}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"unknown"}}\n'
        )
        destination = io.BytesIO()
        serve(source, destination, self.server)
        parse_error, status, unknown = map(json.loads, destination.getvalue().splitlines())
        self.assertEqual(parse_error["error"]["code"], -32700)
        self.assertEqual(json.loads(status["result"]["content"][0]["text"])["name"], "小肥鱼")
        self.assertEqual(unknown["error"]["code"], -32602)
        self.device.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
