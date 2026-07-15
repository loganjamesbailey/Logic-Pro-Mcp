from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_tracked_files_exclude_local_logic_assets_and_private_host_paths() -> None:
    tracked = _tracked_files()
    lowered = {path.as_posix().lower() for path in tracked}

    assert "config/bridge.toml" not in lowered
    assert not any(
        name.endswith((".cst", ".logicx", ".logicx.zip", ".scpt")) for name in lowered
    )
    assert not any(
        path.suffix == ".json"
        and "ax" in path.name.lower()
        and any(word in path.name.lower() for word in ("capture", "dump", "trace"))
        for path in tracked
    )

    private_home_markers = ("/" + "Users/james/", "/" + "home/james/")
    private_key_marker = "BEGIN " + "OPENSSH PRIVATE KEY"
    token_assignment = re.compile(
        r"LOGIC_BRIDGE_TOKEN\s*=\s*['\"](?!\$\()[^'\"\n]{16,}"
    )
    for path in tracked:
        absolute = ROOT / path
        try:
            contents = absolute.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not any(marker in contents for marker in private_home_markers), path
        assert private_key_marker not in contents, path
        assert token_assignment.search(contents) is None, path


def _tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=10,
    )
    return [
        Path(item.decode("utf-8")) for item in completed.stdout.split(b"\0") if item
    ]
