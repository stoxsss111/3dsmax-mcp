import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from maxmcp.max_client import MaxClient


class MultiChatContextTests(unittest.TestCase):
    def test_server_has_connection_instructions(self) -> None:
        from maxmcp import server

        text = server.mcp.instructions or ""
        self.assertIn("list_max_instances", text)
        self.assertIn("max_instance", text)
        self.assertIn("Link to this computer", text)

    def test_registered_tools_run_off_the_event_loop(self) -> None:
        from maxmcp import server

        tool = server.mcp._tool_manager.get_tool("list_max_instances")
        self.assertTrue(tool.is_async)
        self.assertIn("max_instance", tool.parameters["properties"])
        tool = server.mcp._tool_manager.get_tool("execute_maxscript")
        self.assertTrue(tool.is_async)
        self.assertIn("code", tool.parameters["properties"])

    def test_run_in_thread_uses_worker_thread(self) -> None:
        from maxmcp import server

        seen: dict[str, int] = {}

        def blocking(x: int = 1) -> dict:
            seen["thread"] = threading.get_ident()
            return {"x": x}

        runner = server._run_in_thread(blocking)
        result = asyncio.run(runner(x=2))
        self.assertEqual(result, {"x": 2})
        self.assertNotEqual(seen["thread"], threading.get_ident())

    def test_busy_pipe_times_out_instead_of_blocking_forever(self) -> None:
        client = MaxClient(timeout=1.0, transport="pipe")
        pipe = r"\\.\pipe\3dsmax-mcp-test-busy"
        lock = client._pipe_lock_for(pipe)
        lock.acquire()
        try:
            started = time.perf_counter()
            with self.assertRaises(TimeoutError):
                client._send_via_pipe_to(pipe, b"{}\n", time.perf_counter() + 0.2, 0.2)
            self.assertLess(time.perf_counter() - started, 2.0)
        finally:
            lock.release()

    def test_usage_tracking(self) -> None:
        client = MaxClient(timeout=1.0, transport="pipe")
        self.assertEqual(client.instance_usage("p"), {"busy": False, "last_call_s_ago": None})
        client._note_call("p", 1)
        self.assertTrue(client.instance_usage("p")["busy"])
        client._note_call("p", -1)
        usage = client.instance_usage("p")
        self.assertFalse(usage["busy"])
        self.assertIsNotNone(usage["last_call_s_ago"])

    def test_scene_probe_parses_object_count_and_is_not_counted_as_usage(self) -> None:
        client = MaxClient(timeout=1.0, transport="pipe")
        with patch.object(client, "send_command", return_value={"result": "C:\\a\\b.max|12"}):
            scene = client._query_instance_scene("pipeX")
        self.assertEqual(scene, "C:\\a\\b.max")
        self.assertEqual(client._scene_objects["pipeX"], 12)
        self.assertIsNone(client.instance_usage("pipeX")["last_call_s_ago"])

    def test_busy_instance_scene_uses_cache_without_waiting(self) -> None:
        client = MaxClient(timeout=1.0, transport="pipe")
        client._scene_cache["pipeY"] = ("C:\\x.max", 0.0)
        client._note_call("pipeY", 1)
        with patch.object(client, "_query_instance_scene") as query:
            self.assertEqual(client._instance_scene("pipeY", fresh=True), "C:\\x.max")
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
