import json

from ..helpers.error_hints import suggest_tools_for_maxscript
from ..server import mcp, client


_MAXSCRIPT_ERROR_SENTINEL = "__MCP_MS_ERR__:"


# Wrap the user's code so the LAST expression's value comes back in full: the native bridge
# stringifies only strings/numbers and answers a bare "OK" for arrays, point3s, nodes, etc.
# The code stays inside the bridge's own block, so variable scoping is unchanged
# (top-level variables were already local to the call; `global` declarations still work).
_RESULT_WRAP_HEAD = "local __mcp_v = (\n"
_RESULT_WRAP_TAIL = (
    "\n)\n"
    "if classOf __mcp_v == String then __mcp_v "
    "else if __mcp_v == undefined or __mcp_v == ok then \"OK\" "
    "else (with printAllElements on (__mcp_v as string))"
)
_MAX_RESULT_CHARS = 200_000


def wrap_for_full_result(script: str) -> str:
    return _RESULT_WRAP_HEAD + script + _RESULT_WRAP_TAIL


@mcp.tool()
def execute_maxscript(code: str = "", command: str = "", max_instance: str = "", raw: bool = False) -> str:
    """Execute arbitrary MAXScript in 3ds Max and return the result.

    Use when: no dedicated MCP tool covers the operation (custom one-offs, rare APIs).
    Not when: objects, materials, selection, transforms, modifiers, layers, or scene queries —
    prefer the matching dedicated tool instead of raw MAXScript.
    max_instance: optional one-off target when several 3ds Max are open
    (pid, "pid-12345", list index, or part of the scene name); see list_max_instances.
    The value of the last expression is returned in full (arrays, point3, etc.);
    raw=True sends the code unwrapped (old behaviour: non-string results come back as "OK").
    Keep one call under ~50 s; for longer work use run_maxscript_job.
    """
    script = code or command
    if not script:
        return "Error: provide MAXScript code in the 'code' parameter"
    payload_script = script if raw else wrap_for_full_result(script)
    with client.targeting(max_instance):
        response = client.send_command(payload_script, cmd_type="maxscript")
    result = response.get("result", "")
    if isinstance(result, str) and len(result) > _MAX_RESULT_CHARS:
        result = result[:_MAX_RESULT_CHARS] + f"\n... [truncated, {len(result)} chars total]"

    if isinstance(result, str) and result.startswith(_MAXSCRIPT_ERROR_SENTINEL):
        message = result[len(_MAXSCRIPT_ERROR_SENTINEL):].strip()
        payload: dict[str, object] = {
            "status": "error",
            "error_type": "MAXScriptError",
            "error": message,
        }
        suggested = suggest_tools_for_maxscript(script)
        if suggested:
            payload["hint"] = {
                "message": (
                    "execute_maxscript is a fallback. Before retrying the same "
                    "script, consider using a dedicated MCP tool that handles "
                    "this intent directly."
                ),
                "suggested_tools": suggested,
            }
        return json.dumps(payload)

    return result
