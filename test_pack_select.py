import os
import subprocess
import tempfile

obj_content = """# Test OBJ with 2 separate faces
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 5 5 0
v 6 5 0
v 6 6 0
v 5 6 0
vt 0 0
vt 0.5 0
vt 0.5 0.5
vt 0 0.5
vt 0.5 0.5
vt 1 0.5
vt 1 1
vt 0.5 1
f 1/1 2/2 3/3 4/4
f 5/5 6/6 7/7 8/8
"""

with tempfile.NamedTemporaryFile("w", suffix=".obj", delete=False) as f_obj:
    f_obj.write(obj_content)
    obj_path = f_obj.name.replace("\\", "/")

lua_content = f"""ZomLoad({{File={{Path="{obj_path}", ImportGroups=true, XYZ=true}}, NormalizeUVW=true}})
ZomSelect({{PrimType="Polygon", WorkingSet="Visible", Select=true, ResetBefore=true}})
ZomPack({{RootGroup="RootGroup", WorkingSet="Visible", ProcessTileSelection=false, RecursionDepth=1, Translate=true, Rotate={{Min=0, Max=360, Step=90}}, Global={{Scaling={{Mode=3}}}}, LayoutScalingMode=2}})
ZomSave({{File={{Path="{obj_path}", UVWProps=true}}}})
ZomQuit({{}})
"""

with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f_lua:
    f_lua.write(lua_content)
    lua_path = f_lua.name.replace("\\", "/")

rizom = r"C:\Program Files\Rizom Lab\RizomUV 2025.0\rizomuv.exe"
print("Running RizomUV pack test with ZomSelect...")
cmd = f'"{rizom}" -i "{obj_path}" -cfi "{lua_path}"'
proc = subprocess.run(cmd, shell=True, timeout=15)
print("Returncode:", proc.returncode)

with open(obj_path, "r") as f:
    lines = f.readlines()
    print("Output OBJ vt lines:")
    for l in lines:
        if l.startswith("vt "):
            print(l.strip())
