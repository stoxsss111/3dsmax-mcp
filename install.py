#!/usr/bin/env python3
"""One-step installer for 3dsmax-mcp.

Detects 3ds Max, deploys the native bridge, installs MAXScript listener,
builds skills, and registers with AI agents.

Run:  uv run python install.py
Skip skill install: uv run python install.py --skip-skill
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUP_SRCS = {
    2023: ROOT / "native" / "bin" / "mcp_bridge_2023.gup",
    2024: ROOT / "native" / "bin" / "mcp_bridge_2024.gup",
    2025: ROOT / "native" / "bin" / "mcp_bridge_2025.gup",
    2026: ROOT / "native" / "bin" / "mcp_bridge_2026.gup",
    2027: ROOT / "native" / "bin" / "mcp_bridge_2027.gup",
}


MS_SERVER = ROOT / "maxscript" / "mcp_server.ms"
MS_AUTOSTART = ROOT / "maxscript" / "startup" / "mcp_autostart.ms"
CONFIG_SRC = ROOT / "mcp_config.ini"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "3dsmax-mcp"
CONFIG_DST = CONFIG_DIR / "mcp_config.ini"


def pkg_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"

# v0.7.0 standalone chat — .env for the API key, SKILL.md for the system prompt.
ENV_SRC = ROOT / ".env.example"
ENV_DST = CONFIG_DIR / ".env"
SKILL_SRC = ROOT / "skills" / "3dsmax-mcp-dev" / "SKILL.md"
SKILL_DIR = CONFIG_DIR / "skill"
SKILL_DST = SKILL_DIR / "SKILL.md"

# Extract list of supported years from GUP_SRCS
MAX_YEARS = sorted(GUP_SRCS.keys(), reverse=True)

# Find the 3ds Max installation directory for a given year
# First check the environment variable, then the default location (ADSK_3DSMAX_x64_{year})
def max_dir_for_year(year: int) -> Path:
    # Set by 3ds Max installer
    env = os.environ.get(f"ADSK_3DSMAX_x64_{year}", "").strip()
    if env:
        return Path(env)
    # Default location
    return Path(rf"C:\Program Files\Autodesk\3ds Max {year}")


def max_year_for(max_dir: Path) -> int | None:
    for year in MAX_YEARS:
        if max_dir_for_year(year) == max_dir:
            return year
    try:
        return int(max_dir.name.split()[-1])
    except (ValueError, IndexError):
        return None


def gup_src_for(max_dir: Path) -> Path | None:
    year = max_year_for(max_dir)
    if year is None:
        return None
    src = GUP_SRCS.get(year)
    if src and src.exists():
        return src
    return None


def find_max_installations() -> list[Path]:
    return [d for d in (max_dir_for_year(y) for y in MAX_YEARS) if (d / "3dsmax.exe").exists()]


def find_max() -> Path | None:
    found = find_max_installations()
    return found[0] if found else None


def select_max(found: list[Path]) -> Path | None:
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    print("\nMultiple 3ds Max installations found:")
    for i, d in enumerate(found, 1):
        print(f"  {i}) {d}")
    choice = input(f"  Select version [1]: ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(found):
            return found[idx]
    except ValueError:
        pass
    return found[0]


def copy_elevated(src: Path, dst: Path) -> bool:
    """Copy a file, elevating to admin if needed."""
    try:
        shutil.copy2(src, dst)
        return True
    except PermissionError:
        print(f"  Need admin rights for {dst.parent}")
        cmd = f'copy /Y "{src}" "{dst}"'
        result = subprocess.run(
            ["powershell", "-Command",
             f'Start-Process -FilePath cmd.exe -ArgumentList \'/c {cmd}\' -Verb RunAs -Wait'],
            capture_output=True, timeout=30,
        )
        return dst.exists()


def deploy_config(skip_skill: bool = False) -> bool:
    print(f"\n[1/5] User config -> {CONFIG_DIR}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # mcp_config.ini — preserve on redeploy so custom [llm] settings + safe_mode stick
    if CONFIG_DST.exists():
        print(f"  mcp_config.ini: preserved (already exists)")
    elif CONFIG_SRC.exists():
        shutil.copy2(CONFIG_SRC, CONFIG_DST)
        print(f"  mcp_config.ini: installed template")
    else:
        CONFIG_DST.write_text("[mcp]\nsafe_mode = true\n", "utf-8")
        print(f"  mcp_config.ini: created default (safe_mode=true)")

    # .env — preserve on redeploy so the user's API key survives
    if ENV_DST.exists():
        print(f"  .env:           preserved (already exists)")
    elif ENV_SRC.exists():
        shutil.copy2(ENV_SRC, ENV_DST)
        print(f"  .env:           installed template (edit to add OPENROUTER_API_KEY)")
    else:
        print(f"  .env:           SKIP (no .env.example in repo)")

    # SKILL.md — always refresh (source of truth for the in-Max chat's system prompt)
    if skip_skill:
        print(f"  skill/SKILL.md: SKIP (--skip-skill)")
    elif SKILL_SRC.exists():
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SKILL_SRC, SKILL_DST)
        print(f"  skill/SKILL.md: refreshed")
    else:
        print(f"  skill/SKILL.md: SKIP (source not found at {SKILL_SRC})")

    return True


def deploy_native_bridge(max_dir: Path) -> bool:
    plugins_dir = max_dir / "plugins"
    dst = plugins_dir / "mcp_bridge.gup"
    gup_src = gup_src_for(max_dir)
    print(f"\n[2/5] Native bridge -> {dst}")
    year = max_year_for(max_dir)
    if not gup_src:
        expected = GUP_SRCS.get(year) if year else None
        if expected:
            print(f"  SKIP: no native bridge built for 3ds Max {year}")
            print(f"  Expected: {expected}")
        else:
            print(f"  SKIP: unsupported or unknown 3ds Max version")
        print("  MAXScript fallback will still be installed.")
        return False
    print(f"  Using: {gup_src.name}")
    if copy_elevated(gup_src, dst):
        print("  OK")
        return True
    print("  FAILED")
    return False


def deploy_maxscript(max_dir: Path) -> bool:
    print(f"\n[3/5] MAXScript listener (TCP fallback)")
    scripts_dir = max_dir / "scripts"
    mcp_dir = scripts_dir / "mcp"
    startup_dir = scripts_dir / "startup"

    ok = True
    try:
        mcp_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        subprocess.run(
            ["powershell", "-Command",
             f'Start-Process -FilePath cmd.exe -ArgumentList \'/c mkdir "{mcp_dir}"\' -Verb RunAs -Wait'],
            capture_output=True, timeout=30,
        )
    dst1 = mcp_dir / "mcp_server.ms"
    dst2 = startup_dir / "mcp_autostart.ms"

    if copy_elevated(MS_SERVER, dst1):
        print(f"  OK: {dst1}")
    else:
        print(f"  FAILED: {dst1}")
        ok = False

    if copy_elevated(MS_AUTOSTART, dst2):
        print(f"  OK: {dst2}")
    else:
        print(f"  FAILED: {dst2}")
        ok = False

    return ok


def build_skills(skip_skill: bool = False) -> bool:
    print(f"\n[4/5] Building skill files")
    if skip_skill:
        print("  SKIP (--skip-skill)")
        return True
    print("  Where should skills be installed?")
    print("    1) Project only")
    print("    2) Global only (default)")
    print("    3) Both project and global (might cause conflict in some agents)")
    choice = input("  Choice [2]: ").strip()
    target = {"1": "local", "3": "both"}.get(choice, "global")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_skill.py"),
                        "--target", target],
                       check=True, cwd=str(ROOT))
        print("  OK")
        return True
    except subprocess.CalledProcessError:
        print("  FAILED")
        return False


def mcp_server_entry(repo_dir: str) -> dict:
    return {"command": "uv", "args": ["run", "--directory", repo_dir, "3dsmax-mcp"]}


def claude_desktop_config_paths() -> list[Path]:
    """Return Claude Desktop config paths for classic and Microsoft Store installs."""
    paths: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    packages_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    if packages_dir.is_dir():
        for pkg_dir in sorted(packages_dir.glob("Claude_*")):
            add(
                pkg_dir
                / "LocalCache"
                / "Roaming"
                / "Claude"
                / "claude_desktop_config.json"
            )

    add(Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json")
    return paths


def _claude_desktop_label(config_path: Path) -> str:
    parts = {part.lower() for part in config_path.parts}
    if "packages" in parts and any(part.lower().startswith("claude_") for part in config_path.parts):
        return "Claude Desktop (Microsoft Store)"
    return "Claude Desktop"


def app_mcp_config_paths() -> list[tuple[str, Path]]:
    targets = [(_claude_desktop_label(path), path) for path in claude_desktop_config_paths()]
    targets.extend(
        [
            ("Gemini", Path.home() / ".gemini" / "settings.json"),
            ("Cursor", Path.home() / ".cursor" / "mcp.json"),
        ]
    )
    return targets


def warn_if_uv_missing() -> None:
    if shutil.which("uv"):
        return
    print("  WARNING: 'uv' not found on PATH.")
    print("  Claude Desktop / Cursor may fail to launch the MCP server until uv is available.")
    print("  Install with: pip install uv")
    print("  If uv is already installed, add your Python Scripts folder to PATH.")


def register_app_mcp_configs(repo_dir: str) -> None:
    entry = mcp_server_entry(repo_dir)
    for label, config_path in app_mcp_config_paths():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            print(f"  SKIP: {label} (cannot create {config_path.parent})")
            continue
        try:
            config = json.loads(config_path.read_text("utf-8")) if config_path.exists() else {}
        except Exception:
            config = {}
        servers = config.setdefault("mcpServers", {})
        if servers.get("3dsmax-mcp") != entry:
            servers["3dsmax-mcp"] = entry
            config_path.write_text(json.dumps(config, indent=2) + "\n", "utf-8")
            print(f"  OK: {label} ({config_path})")
        else:
            print(f"  Already up to date: {label}")


def register_agents() -> bool:
    print(f"\n[5/5] Agent registration")
    dir_str = str(ROOT)

    # Detect which agents are installed
    agents = []
    for name in ["claude", "codex", "gemini"]:
        if shutil.which(name):
            agents.append(name)

    if not agents:
        print("  No agents found on PATH (claude, codex, gemini)")
        print("  Manual registration:")
        print(f'    claude mcp add --scope user 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp')
        print(f'    Cursor: {Path.home() / ".cursor" / "mcp.json"} (updated below if writable)')

    for agent in agents:
        # Each agent CLI has different syntax
        if agent == "claude":
            cmd = f'{agent} mcp add --scope user 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp'
        elif agent == "codex":
            cmd = f'{agent} mcp add 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp'
        elif agent == "gemini":
            cmd = f'{agent} mcp add --scope user 3dsmax-mcp uv run --directory "{dir_str}" 3dsmax-mcp'
        else:
            continue
        print(f"  Registering with {agent}...")
        try:
            subprocess.run(cmd, shell=True, check=True, capture_output=True, timeout=15)
            print(f"  OK: {agent}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(f"  SKIP: {agent} (run manually: {cmd})")

    warn_if_uv_missing()

    # App configs that store mcpServers (Claude Desktop, Gemini, Cursor)
    register_app_mcp_configs(dir_str)

    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install 3dsmax-mcp")
    parser.add_argument(
        "--skip-skill",
        "--skip-skills",
        action="store_true",
        help="Skip copying SKILL.md to the chat config and skip build_skill.py.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print(f"  3dsmax-mcp installer v{pkg_version()}")
    print("=" * 60)

    # Find Max
    found = find_max_installations()
    if found:
        max_dir = select_max(found)
        print(f"\nUsing 3ds Max at: {max_dir}")
    else:
        print("\n3ds Max not found in default locations.")
        custom = input("Enter 3ds Max install path (or press Enter to skip): ").strip()
        if custom:
            max_dir = Path(custom)
            if not (max_dir / "3dsmax.exe").exists():
                print(f"  3dsmax.exe not found in {max_dir}")
                max_dir = None
        else:
            max_dir = None

    # Deploy
    deploy_config(skip_skill=args.skip_skill)
    native_ok = False
    if max_dir:
        native_ok = deploy_native_bridge(max_dir)
        deploy_maxscript(max_dir)
    else:
        print("\n[2/5] SKIP: no Max installation")
        print("[3/5] SKIP: no Max installation")

    build_skills(skip_skill=args.skip_skill)
    register_agents()

    # Summary
    print("\n" + "=" * 60)
    print("  done!")
    print("=" * 60)
    if max_dir:
       if native_ok:
           print(f"\n  restart 3dsmax to load the native bridge.")
       else:
           print(f"\n  native bridge was not installed; 3dsmax will use MAXScript fallback.")
    print(f"  the MCP server starts automatically when your agent connects.")
    print(f"\n ")
    print(f"\n  and thank you for installing 3dsmax-mcp! I hope you enjoy it! 3dsmax forever!!")

    print(f"\n  clone // Metaverse Makers. 2026")
    print()


if __name__ == "__main__":
    main()
