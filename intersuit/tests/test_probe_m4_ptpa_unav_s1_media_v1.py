"""UnAV S1 官方命令匿名媒体probe测试。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/probe_m4_ptpa_unav_s1_media_v1.py"
SPEC = importlib.util.spec_from_file_location("probe_m4_ptpa_unav_s1_media_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _row(video_id: str, split: str) -> dict:
    return {
        "physical_media_id": video_id,
        "official_split": split,
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "anonymous_probe_allowed": True,
        "media_download_allowed": False,
        "effect_access_allowed": False,
    }


def test_select_canary2_takes_one_per_split():
    rows = [_row("train00001", "train"), _row("train00002", "train"), _row("valid00001", "validation")]
    selected = module.select_canary2(rows)
    assert [row["physical_media_id"] for row in selected] == ["train00001", "valid00001"]


def test_probe_uses_official_base_command_without_credentials(monkeypatch, tmp_path):
    ytdlp = tmp_path / "yt-dlp"
    ytdlp.write_text("test", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="train00001\n", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.probe([_row("train00001", "train")], ytdlp)

    assert result["status"] == "PASS"
    assert calls[0][1:] == [
        "--ignore-config",
        "https://www.youtube.com/watch?v=train00001",
        "-f",
        "b",
        "--simulate",
        "--quiet",
        "--print",
        "id",
    ]
    assert not any("cookie" in value.lower() for value in calls[0])
    assert result["media_files_created"] == 0


def test_classify_page_reload_failure():
    assert module.classify_failure("ERROR: [youtube] page needs to be reloaded") == "page_reload"
    assert module.classify_failure("ERROR: Requested format is not available") == "requested_format_unavailable"


def test_parse_args_exposes_yt_dlp_field(monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--canary2"])
    args = module.parse_args()
    assert args.yt_dlp == module.DEFAULT_YTDLP
    assert args.canary2 is True


def test_compat_probe_adds_node_and_split_formats_without_credentials(monkeypatch, tmp_path):
    ytdlp = tmp_path / "yt-dlp"
    node = tmp_path / "node"
    ytdlp.write_text("test", encoding="utf-8")
    node.write_text("test", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="train00001\n", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.probe(
        [_row("train00001", "train")], ytdlp, compat=True, node=node
    )
    assert result["status"] == "PASS"
    assert "--js-runtimes" in calls[0]
    assert "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio" in calls[0]
    assert not any("cookie" in value.lower() for value in calls[0])
