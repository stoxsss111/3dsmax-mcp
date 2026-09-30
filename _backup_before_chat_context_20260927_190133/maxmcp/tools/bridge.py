"""Bridge/transport status tools for the live 3ds Max connection."""

from __future__ import annotations

import json

from ..server import mcp, client


def _legacy_bridge_status() -> str:
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString
        local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
        local rendererName = try ((classOf renderers.current) as string) catch "unknown"
        "{\"pong\":true" + \
        ",\"server\":\"3dsmax-mcp\"" + \
        ",\"protocolVersion\":1" + \
        ",\"maxVersion\":" + (maxYear as string) + \
        ",\"renderer\":\"" + (esc rendererName) + "\"" + \
        ",\"objectCount\":" + (objects.count as string) + \
        ",\"selectionCount\":" + (selection.count as string) + \
        ",\"safeMode\":" + (if MCP_Server.safeMode then "true" else "false") + \
        ",\"port\":" + (MCP_Server.port as string) + \
        "}"
    )"""
    response = client.send_command(maxscript, timeout=5.0)
    payload = json.loads(response.get("result", "{}"))
    payload["requestId"] = response.get("requestId")
    payload["meta"] = response.get("meta", {})
    payload["connected"] = True
    payload["legacyTransport"] = True
    return json.dumps(payload)


@mcp.tool()
def get_bridge_status() -> str:
    """Ping the MCP bridge for protocol/transport metadata.

    Use when: a tool failed with a connection/transport/claim error and you need to diagnose.
    Not when: starting a session or before every task — prefer query_scene for scene work.
    """
    try:
        response = client.send_command("", cmd_type="ping", timeout=5.0)
    except RuntimeError as exc:
        error = str(exc)
        if "Empty command" in error or "Unknown command type" in error:
            return _legacy_bridge_status()
        raise

    payload = json.loads(response.get("result", "{}"))
    payload["requestId"] = response.get("requestId")
    payload["meta"] = response.get("meta", {})
    payload["connected"] = True
    payload["legacyTransport"] = False
    return json.dumps(payload)


@mcp.tool()
def list_max_instances(include_scene: bool = True) -> str:
    """List every running 3ds Max that has the MCP bridge loaded.

    Use when: several 3ds Max windows are open, a tool failed with an
    "Multiple 3ds Max MCP instances" error, or before select_max_instance.
    Returns index, pid, instance_id, open scene, and which one is claimed/selected.
    """
    items = client.list_instances(details=include_scene, fresh=True)
    return json.dumps({
        "count": len(items),
        "note": (
            "This MCP server process is shared by all chats of the desktop app. "
            "When more than one 3ds Max is open, pass max_instance=<pid or scene name part> "
            "on every tool call instead of relying on select_max_instance."
        ),
        "instances": [
            {
                "index": item.get("index"),
                "pid": item.get("pid"),
                "instance_id": item.get("instance_id"),
                "scene": item.get("scene"),
                "claimed": item.get("claimed", False),
                "selected": item.get("selected", False),
                "pipe": item.get("pipe"),
            }
            for item in items
        ],
    })


@mcp.tool()
def select_max_instance(instance: str = "") -> str:
    """Bind this MCP session to one 3ds Max instance (all later tools go there).

    instance: list index ("1"), pid ("12345"), instance id ("pid-12345"),
    or part of the open scene file name ("2326"). Empty / "auto" = back to
    default routing (claimed instance, or the only one running).
    WARNING: the desktop app runs ONE MCP server process for ALL chats, so this
    selection is global and another chat can override it. With several 3ds Max
    open, pass max_instance=<pid or scene name part> on every tool call instead.
    """
    item = client.select_instance(instance)
    if item is None:
        return json.dumps({"selected": None, "mode": "auto"})
    return json.dumps({
        "shared_across_chats": True,
        "selected": {
            "pid": item.get("pid"),
            "instance_id": item.get("instance_id"),
            "scene": item.get("scene"),
            "pipe": item.get("pipe"),
        }
    })
