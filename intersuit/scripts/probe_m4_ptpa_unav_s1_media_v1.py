#!/usr/bin/env python3
"""按 UnAV 官方 yt-dlp 命令合同匿名探测 train/validation 媒体 canary。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_PLAN = (
    INTERSUIT_ROOT
    / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_media_canary20_plan_locked.jsonl"
)
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/media_probe_v1/canary2_probe.json"
DEFAULT_YTDLP = Path("/home/yjm/miniconda3/envs/llp-download/bin/yt-dlp")
DEFAULT_NODE = Path("/home/yjm/miniconda3/envs/llp-download/bin/node")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise ValueError("UnAV canary plan 为空")
    return rows


def classify_failure(stderr: str) -> str:
    lowered = stderr.lower()
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "bot_authentication"
    if "this video is unavailable" in lowered or "private video" in lowered:
        return "unavailable"
    if "page needs to be reloaded" in lowered:
        return "page_reload"
    if "requested format is not available" in lowered:
        return "requested_format_unavailable"
    if "http error 403" in lowered:
        return "http_403"
    return "other"


def select_canary2(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for split in ("train", "validation"):
        selected = [row for row in rows if row.get("official_split") == split]
        if not selected:
            raise ValueError(f"UnAV canary plan 缺少 {split}")
        output.append(selected[0])
    return output


def validate_inputs(contract: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    permissions = contract["permissions"]
    downloader = contract["official_downloader"]
    if permissions.get("anonymous_train_validation_probe_allowed") is not True:
        raise PermissionError("UnAV anonymous probe 未授权")
    if permissions.get("train_validation_media_download_allowed") is not False:
        raise PermissionError("UnAV probe 阶段下载权限必须关闭")
    if downloader.get("cookies_allowed") is not False:
        raise PermissionError("UnAV probe 禁止 cookie")
    if downloader.get("client_or_po_token_args_allowed") is not False:
        raise PermissionError("UnAV probe 禁止 client/PO token 参数")
    if len(rows) != 20:
        raise ValueError("UnAV canary20 plan 必须恰好20行")
    if {row["official_split"] for row in rows} != {"train", "validation"}:
        raise ValueError("UnAV probe 只允许 train/validation")
    for row in rows:
        if (
            row.get("anonymous_probe_allowed") is not True
            or row.get("media_download_allowed") is not False
            or row.get("effect_access_allowed") is not False
        ):
            raise PermissionError("UnAV plan 权限字段漂移")


def probe(
    rows: list[dict[str, Any]], ytdlp: Path, timeout: int = 45,
    compat: bool = False, node: Path | None = None,
) -> dict[str, Any]:
    if not ytdlp.is_file():
        raise FileNotFoundError(f"yt-dlp 不存在：{ytdlp}")
    results = []
    for row in rows:
        command = [
            str(ytdlp),
            "--ignore-config",
            row["source_url"],
        ]
        if compat:
            if node is None or not node.is_file():
                raise FileNotFoundError(f"Node runtime 不存在：{node}")
            command.extend(
                [
                    "--js-runtimes",
                    f"node:{node}",
                    "-f",
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio",
                ]
            )
        else:
            command.extend(["-f", "b"])
        command.extend([
            "--simulate",
            "--quiet",
            "--print",
            "id",
        ])
        proc = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
        success = proc.returncode == 0 and row["physical_media_id"] in proc.stdout
        results.append(
            {
                "physical_media_id": row["physical_media_id"],
                "official_split": row["official_split"],
                "status": "available" if success else "failed",
                "failure_class": None if success else classify_failure(proc.stderr),
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout.strip()[-300:],
                "stderr_tail": proc.stderr.strip()[-1200:],
                "cookies_used": False,
                "client_or_po_token_args_used": False,
                "media_bytes_created": 0,
            }
        )
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    return {
        "artifact_kind": "m4_ptpa_unav_s1_anonymous_media_probe",
        "format_version": 1,
        "status": "PASS" if all(row["status"] == "available" for row in results) else "FAIL",
        "requested": len(results),
        "available": sum(row["status"] == "available" for row in results),
        "failed": sum(row["status"] == "failed" for row in results),
        "failure_classes": dict(
            sorted(
                __import__("collections").Counter(
                    row["failure_class"] for row in results if row["failure_class"]
                ).items()
            )
        ),
        "media_files_created": 0,
        "cookies_used": False,
        "client_or_po_token_args_used": False,
        "probe_mode": "node_ejs_compat" if compat else "literal_official",
        "results": results,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    rows = read_jsonl(args.plan)
    validate_inputs(contract, rows)
    selected = select_canary2(rows) if args.canary2 else rows
    result = probe(
        selected, args.yt_dlp, timeout=args.timeout,
        compat=args.compat, node=args.node,
    )
    result["contract"] = str(args.contract.resolve())
    result["plan"] = str(args.plan.resolve())
    result["yt_dlp"] = str(args.yt_dlp.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--yt-dlp", type=Path, default=DEFAULT_YTDLP)
    parser.add_argument("--node", type=Path, default=DEFAULT_NODE)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--canary2", action="store_true")
    parser.add_argument("--compat", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(json.dumps({key: result[key] for key in ("status", "requested", "available", "failed")}, ensure_ascii=False))
