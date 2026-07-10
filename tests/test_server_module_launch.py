import os
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ServerModuleLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def _list_tool_names(self, args: list[str]) -> list[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env={**os.environ, "MCP_TOOL_PROFILE": "core"},
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                return [tool.name for tool in tools]

    async def test_module_launch_registers_tools(self) -> None:
        tool_names = await self._list_tool_names(["-m", "src.server"])

        self.assertIn("execute_maxscript", tool_names)
        self.assertIn("query_scene", tool_names)
        self.assertIn("get_material_library", tool_names)
        self.assertIn("backup_material_library", tool_names)
        self.assertGreater(len(tool_names), 0)


if __name__ == "__main__":
    unittest.main()
