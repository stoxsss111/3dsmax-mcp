import os
import subprocess
import tempfile

obj_content = """# Test OBJ
v -1 -1 0
v 1 -1 0
v 1 1 0
v -1 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
f 1/1 2/2 3/3 4/4
"""

with tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False) as f_obj:
    f_obj.write(obj_content)
    obj_path = f_obj.name.replace("\\", "/")

lua_content = f"""ZomLoad({{File={{Path="{obj_path}", ImportGroups=true, XYZ=true}}, NormalizeUVW=true}})
ZomSelect({{PrimType="Polygon", WorkingSet="Visible", Select=true, ResetBefore=true}})
ZomUnfold({{WorkingSet="Visible&UnLocked", BorderIntersections=true, TriangleFlips=true}})
ZomPack({{RootGroup="RootGroup", WorkingSet="Visible", ProcessTileSelection=false, RecursionDepth=1, Translate=true, Global={{Scaling={{Mode=3}}}}, LayoutScalingMode=2}})
ZomSave({{File={{Path="{obj_path}", UVWProps=true}}}})
ZomQuit({{}})
"""

with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f_lua:
    f_lua.write(lua_content)
    lua_path = f_lua.name.replace("\\", "/")

rizom = r"C:\Program Files\Rizom Lab\RizomUV 2025.0\rizomuv.exe"
print("Testing /i /cfi flags...")
print("OBJ path:", obj_path)
print("Lua path:", lua_path)

proc = subprocess.Popen([rizom, "/i", "/cfi", lua_path])
try:
    proc.wait(timeout=5)
    print("SUCCESS! Exited in under 5 seconds! Code:", proc.returncode)
    print("OBJ size:", os.path.getsize(obj_path))
except subprocess.TimeoutExpired:
    print("TIMED OUT! Killing process...")
    proc.kill()
