"""MaxFast (.NET) tools: background MAXScript jobs, fast batched ray casts, fast triangle export.

MaxFast.dll is a small C# assembly loaded into 3ds Max (Desktop/VibeScripts/maxfast). Its entry
points catch their own exceptions, so a bad call returns an error instead of taking Max down.
"""
import json

from ..server import mcp, client

_LOADER = r"C:\Users\sapfi\Desktop\VibeScripts\maxfast\maxfast.ms"


def _ms_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t") + '"'


def _run(body: str, max_instance: str = "") -> str:
    # The bridge wraps every script in a block: names that are not yet globals when the
    # script is compiled would become block locals, so declare the MaxFast globals first.
    script = (
        "global mfEnsure, mfInts, mfFloats, mfStrings, MF_Info, MF_Geo, MF_Rays, MF_Grass, MF_Jobs, gxExportMft\n"
        f'if mfEnsure == undefined do fileIn {_ms_str(_LOADER)}\n'
        "mfEnsure()\n" + body
    )
    with client.targeting(max_instance):
        response = client.send_command(script, cmd_type="maxscript")
    result = response.get("result", "")
    return result if isinstance(result, str) else str(result)


@mcp.tool()
def run_maxscript_job(tasks: list[str], interval_ms: int = 50, max_instance: str = "") -> str:
    """Queue MAXScript snippets to run one by one in the background inside 3ds Max.

    Use when: work is longer than ~50 s (the bridge limit) - split it into tasks of a few
    seconds each (e.g. one object / one chunk per task). The call returns at once; Max stays
    responsive between tasks; poll job_status, stop with job_cancel. Tasks run in global scope
    (use globals / fileIn'd functions to share state). The first failing task stops the queue.
    """
    if not tasks:
        return "Error: no tasks"
    arr = "#(" + ", ".join(_ms_str(t) for t in tasks) + ")"
    return _run(f"MF_Jobs.Start (mfStrings {arr}) {int(interval_ms)}", max_instance)


@mcp.tool()
def job_status(last_n: int = 5, max_instance: str = "") -> str:
    """State of the background MAXScript job queue: state done/total, queued, last results."""
    return _run(f"MF_Jobs.Status {int(last_n)}", max_instance)


@mcp.tool()
def job_cancel(max_instance: str = "") -> str:
    """Cancel the background MAXScript job queue (the task currently running finishes first)."""
    return _run("MF_Jobs.Cancel()", max_instance)


@mcp.tool()
def build_ray_scene(node_names: list[str], max_instance: str = "") -> str:
    """Build a fast ray-cast structure (BVH) from the evaluated world meshes of the given nodes.

    Use before raycast for many rays (thousands to millions); kept in memory until rebuilt.
    """
    arr = "#(" + ", ".join(_ms_str(n) for n in node_names) + ")"
    body = (
        f"local ns = for n in {arr} collect (getNodeByName n)\n"
        "local miss = for i = 1 to ns.count where ns[i] == undefined collect i\n"
        "local hs = for n in ns where n != undefined collect n.inode.handle\n"
        "(MF_Rays.Build (mfInts hs)) + \" missing:\" + miss.count as string"
    )
    return _run(body, max_instance)


@mcp.tool()
def raycast(origins: list[list[float]], directions: list[list[float]] | None = None, max_dist: float = 1e9,
            max_instance: str = "") -> str:
    """Cast rays against the scene built by build_ray_scene (exact triangles, parallel C#).

    origins: [[x,y,z],...]; directions: same length, or omitted = straight down [0,0,-1].
    Returns JSON list of [distance or -1, hit node name or ""] per ray.
    """
    if not origins:
        return "[]"
    dirs = directions or [[0.0, 0.0, -1.0]] * len(origins)
    if len(dirs) != len(origins):
        return "Error: directions must match origins"
    o = ",".join(f"{p[0]},{p[1]},{p[2]}" for p in origins)
    d = ",".join(f"{p[0]},{p[1]},{p[2]}" for p in dirs)
    body = (
        f"local R = MF_Rays.Cast (mfFloats #({o})) (mfFloats #({d})) {float(max_dist)}\n"
        "local ss = stringstream \"\"\n"
        "for i = 1 to R.count by 2 do format \"[%,\\\"%\\\"]%\" R[i] (MF_Rays.ObjectName (R[i+1] as integer)) (if i + 1 < R.count then \",\" else \"\") to:ss\n"
        "\"[\" + (ss as string) + \"]\""
    )
    return _run(body, max_instance)


@mcp.tool()
def export_ground_tris(lawn_names: list[str], out_file: str, zband: float = 300.0, max_instance: str = "") -> str:
    """Export lawn triangles + 2D footprints of everything touching the ground near them (MFT1 binary).

    Same selection as the grass pipeline (gx_lib.ms); ~0.5 s for ~1M triangles. Read with gx_mft.py.
    """
    arr = "#(" + ", ".join(_ms_str(n) for n in lawn_names) + ")"
    body = (
        'if gxExportMft == undefined do fileIn @"C:\\Users\\sapfi\\Desktop\\VibeScripts\\grass_scatter\\gx_lib.ms"\n'
        f"gxExportMft {arr} {_ms_str(out_file)} zband:{float(zband)}"
    )
    return _run(body, max_instance)
