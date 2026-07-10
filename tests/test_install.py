from pathlib import Path

import install


def test_max_year_for_reads_standard_install_folder_names() -> None:
    assert install.max_year_for(Path(r"C:\Program Files\Autodesk\3ds Max 2023")) == 2023
    assert install.max_year_for(Path(r"C:\Program Files\Autodesk\3ds Max 2027")) == 2027
    assert install.max_year_for(Path(r"C:\weird\Max")) is None


def test_max_year_for_uses_installer_env_var_for_custom_paths(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-max"
    custom.mkdir()
    monkeypatch.setenv("ADSK_3DSMAX_x64_2025", str(custom))
    assert install.max_year_for(custom) == 2025


def test_find_max_installations_uses_env_var_path(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-max-2026"
    custom.mkdir()
    (custom / "3dsmax.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("ADSK_3DSMAX_x64_2026", str(custom))
    # Pin to one year so real installs on the host machine don't leak in
    monkeypatch.setattr(install, "MAX_YEARS", [2026])
    assert install.find_max_installations() == [custom]


def test_max_dir_for_year_prefers_env_over_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADSK_3DSMAX_x64_2025", str(tmp_path))
    assert install.max_dir_for_year(2025) == tmp_path


def test_max_dir_for_year_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("ADSK_3DSMAX_x64_2025", raising=False)
    assert install.max_dir_for_year(2025) == Path(r"C:\Program Files\Autodesk\3ds Max 2025")


def test_native_bridge_sources_are_exact_versioned_binaries() -> None:
    for year in (2023, 2024, 2025, 2026, 2027):
        assert install.GUP_SRCS[year].name == f"mcp_bridge_{year}.gup"
        assert install.gup_src_for(Path(fr"C:\Program Files\Autodesk\3ds Max {year}")) == install.GUP_SRCS[year]

    assert install.gup_src_for(Path(r"C:\Program Files\Autodesk\3ds Max 2028")) is None


def test_claude_desktop_config_paths_include_store_and_classic(monkeypatch, tmp_path: Path) -> None:
    local_app = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    store_pkg = local_app / "Packages" / "Claude_pzs8sxrjxfjjc"
    store_config = store_pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
    store_config.parent.mkdir(parents=True)

    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("APPDATA", str(roaming))

    paths = install.claude_desktop_config_paths()
    assert store_config in paths
    assert paths[-1] == roaming / "Claude" / "claude_desktop_config.json"


def test_app_mcp_config_paths_includes_cursor_and_store_claude(monkeypatch, tmp_path: Path) -> None:
    local_app = tmp_path / "LocalAppData"
    (local_app / "Packages" / "Claude_testpkg" / "LocalCache" / "Roaming" / "Claude").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    labels = [label for label, _ in install.app_mcp_config_paths()]
    paths = [path for _, path in install.app_mcp_config_paths()]
    assert "Claude Desktop (Microsoft Store)" in labels
    assert "Cursor" in labels
    assert paths[labels.index("Cursor")] == Path.home() / ".cursor" / "mcp.json"


def test_mcp_server_entry_uses_uv_run() -> None:
    entry = install.mcp_server_entry(r"C:\repo\3dsmax-mcp")
    assert entry == {
        "command": "uv",
        "args": ["run", "--directory", r"C:\repo\3dsmax-mcp", "3dsmax-mcp"],
    }
