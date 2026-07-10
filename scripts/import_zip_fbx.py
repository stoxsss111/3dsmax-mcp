import os
import sys
import zipfile
import ctypes
from pathlib import Path

# Add 3dsmax-mcp src folder to path to import MaxClient
sys.path.append(str(Path(r"C:\Users\sapfi\Desktop\VibeScripts\3dsmax-mcp\src")))
from max_client import MaxClient

def msg_box(title, text, style=0):
    return ctypes.windll.user32.MessageBoxW(0, text, title, style)

def main():
    if len(sys.argv) < 2:
        msg_box("Import ZIP to 3ds Max", "No file specified.", 16)  # MB_ICONERROR
        return

    zip_path = sys.argv[1]
    if not os.path.exists(zip_path):
        msg_box("Import ZIP to 3ds Max", f"File does not exist:\n{zip_path}", 16)
        return

    # Extract directory next to the ZIP
    zip_dir = os.path.dirname(zip_path)
    zip_name = os.path.splitext(os.path.basename(zip_path))[0]
    extract_dir = os.path.join(zip_dir, zip_name)

    try:
        # Create directory
        os.makedirs(extract_dir, exist_ok=True)
        
        # Extract ZIP
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
            
    except Exception as e:
        msg_box("Import ZIP to 3ds Max", f"Failed to extract ZIP archive:\n{str(e)}", 16)
        return

    # Find FBX files
    fbx_files = []
    for root, dirs, files in os.walk(extract_dir):
        for file in files:
            if file.lower().endswith(".fbx"):
                fbx_files.append(os.path.join(root, file))

    if not fbx_files:
        msg_box("Import ZIP to 3ds Max", "No FBX files found inside the ZIP archive.", 48)  # MB_ICONWARNING
        return

    # Connect to 3ds Max and import
    client = MaxClient()
    imported_count = 0
    failed_imports = []

    for fbx_file in fbx_files:
        # Format path with double backslashes for MAXScript
        escaped_path = fbx_file.replace("\\", "\\\\")
        cmd = f'importFile @"{escaped_path}" #noPrompt'
        
        try:
            res = client.send_command(cmd)
            if res.get("success"):
                imported_count += 1
            else:
                failed_imports.append(os.path.basename(fbx_file))
        except Exception as e:
            failed_imports.append(f"{os.path.basename(fbx_file)} ({str(e)})")

    if failed_imports:
        err_msg = "Failed to import some FBX files:\n" + "\n".join(failed_imports)
        msg_box("Import ZIP to 3ds Max", err_msg, 16)
    elif imported_count == 0:
        msg_box("Import ZIP to 3ds Max", "Could not connect to 3ds Max. Please make sure 3ds Max is running and the MCP bridge is active.", 16)

if __name__ == "__main__":
    main()
