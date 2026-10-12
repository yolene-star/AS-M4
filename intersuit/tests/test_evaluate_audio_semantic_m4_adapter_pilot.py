from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_adapter_baseline_eval_fails_before_model_load_when_closed(tmp_path):
    root = Path(__file__).parents[1]
    contract = tmp_path / 'contract.json'
    manifest = tmp_path / 'manifest.jsonl'
    contract.write_text(json.dumps({'authorization':{'gpu_baseline_eval':False}}),encoding='utf-8')
    manifest.write_text('{}\n',encoding='utf-8')
    result = subprocess.run(
        [sys.executable,str(root/'scripts/evaluate_audio_semantic_m4_adapter_pilot.py'),
         '--contract',str(contract),'--manifest',str(manifest),'--output-dir',str(tmp_path/'out')],
        cwd=root,text=True,capture_output=True,
    )
    assert result.returncode != 0 and '尚未授权 gpu_baseline_eval' in result.stderr
    assert not (tmp_path/'out').exists()
