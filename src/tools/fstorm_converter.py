"""FStorm Renderer & Material Converter Tool for 3ds Max MCP."""

from pathlib import Path
from ..server import mcp, client
from .material_ops import _ms_path


@mcp.tool()
def convert_to_fstorm(
    convert_materials: bool = True,
    convert_lights: bool = True,
    convert_bitmaps: bool = True,
    set_renderer: bool = True,
    target_scope: str = "all",
) -> dict:
    """Convert scene materials, textures, lights, and renderer settings to FStorm Render format.
    
    Args:
        convert_materials: Convert PhysicalMaterial, Standard, VRay, Corona to FStorm material.
        convert_lights: Convert Standard/VRay/Corona lights to FStormLight.
        convert_bitmaps: Convert Bitmaps/VRayBitmaps to FStormBitmap.
        set_renderer: Set current render engine to FStorm.
        target_scope: 'all' for full scene, or 'selected' for active selection.
    """
    converter_script_path = Path(r"c:\Users\sapfi\Desktop\VibeScripts\FStormConverter.ms")
    ms_path = _ms_path(converter_script_path)
    
    scope_symbol = "#selected" if target_scope.lower() == "selected" else "#all"
    mats_bool = "true" if convert_materials else "false"
    lights_bool = "true" if convert_lights else "false"
    bitmaps_bool = "true" if convert_bitmaps else "false"
    renderer_bool = "true" if set_renderer else "false"
    
    cmd = f"""
(
    fileIn @"{ms_path}"
    local res = convertSceneToFStorm convertMats:{mats_bool} convertLights:{lights_bool} convertBitmaps:{bitmaps_bool} setRenderer:{renderer_bool} targetObjects:{scope_symbol}
    res
)
"""
    res = client.send_command(cmd)
    if res.get("success"):
        return {
            "success": True,
            "result": res.get("result"),
            "details": f"FStorm conversion executed (scope={target_scope}, materials={convert_materials}, lights={convert_lights})"
        }
    else:
        return {
            "success": False,
            "error": res.get("error", "Unknown error during FStorm conversion")
        }
