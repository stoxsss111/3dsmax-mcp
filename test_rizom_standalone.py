import os
import sys
from src.max_client import MaxClient

client = MaxClient()
print("Connected to 3ds Max MCP bridge:", client.native_available)

test_script = r"""
(
    -- Create test cylinder with UVs
    local c = Cylinder name:"RizomPackTest" radius:10 height:30 sides:12
    addModifier c (Uvwmap())
    convertToPoly c
    select c
    
    local tempObjPath = (getDir #temp) + "\\rizom_pack_test.obj"
    local tempLuaPath = (getDir #temp) + "\\rizom_pack_test.lua"
    
    exportFile tempObjPath #noPrompt selectedOnly:true
    
    local f = createFile tempLuaPath
    local luaMeshPath = substituteString tempObjPath "\\" "/"
    format "ZomLoad({File={Path=\"%\", ImportGroups=true, XYZ=true}, NormalizeUVW=true})\n" luaMeshPath to:f
    format "ZomSelect({PrimType=\"Polygon\", WorkingSet=\"Visible\", Select=true, ResetBefore=true})\n" to:f
    format "ZomUnfold({WorkingSet=\"Visible&UnLocked\", BorderIntersections=true, TriangleFlips=true})\n" to:f
    format "ZomPack({RootGroup=\"RootGroup\", WorkingSet=\"Visible\", ProcessTileSelection=false, RecursionDepth=1, Translate=true, Global={Scaling={Mode=3}}, LayoutScalingMode=2})\n" to:f
    format "ZomSave({File={Path=\"%\", UVWProps=true}})\n" luaMeshPath to:f
    format "ZomQuit({AskToSave=false})\n" to:f
    close f
    
    local rizomExe = @"C:\Program Files\Rizom Lab\RizomUV 2025.0\rizomuv.exe"
    local sysProc = dotnetClass "System.Diagnostics.Process"
    local args = "-cfi \"" + tempLuaPath + "\""
    local startInfo = dotnetObject "System.Diagnostics.ProcessStartInfo" rizomExe args
    startInfo.UseShellExecute = false
    startInfo.CreateNoWindow = true
    
    local proc = sysProc.Start startInfo
    proc.WaitForExit(15000)
    if not proc.HasExited do proc.Kill()
    
    delete c
    format "OBJ file size after pack: %\n" (getFileSize tempObjPath)
)
"""

print("Executing test script in 3ds Max...")
res = client.send_command(test_script, cmd_type="maxscript")
print("Result:", res)
