import json
from typing import Any

from ..coerce import StrList
from ..server import mcp, client


@mcp.tool()
def advancedvision(
    action: str = "show",
    components: StrList | None = None,
    target: str = "",
    max_ids: int = 200,
    id_base: int = 1,
    text_size: float = 9.0,
    hud: bool = False,
) -> Any:
    """Single compact visual-assist tool for viewport-editing experiments.

    Initial actions:
    - show: draw compact vertex/edge/optional-face IDs on the selected MNMesh output
    - hide: remove the overlay
    - toggle: switch overlay visibility
    - status: report current overlay and selected mesh counts

    components defaults to ["vertices", "edges"]. Use ["faces"] only when needed.
    """
    payload: dict[str, Any] = {
        "action": action,
        "target": target,
        "max_ids": max(0, int(max_ids)),
        "id_base": 0 if int(id_base) == 0 else 1,
        "text_size": float(text_size),
        "hud": bool(hud),
    }
    if components:
        payload["components"] = list(components)
    elif action.lower() in {"show", "on", "enable", "toggle"}:
        payload["components"] = ["vertices", "edges"]
    response = client.send_command(json.dumps(payload), cmd_type="native:advancedvision")
    return json.loads(response.get("result", "{}"))
