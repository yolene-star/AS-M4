from pathlib import Path
import subprocess,sys

def test_adapter_pilot_train_entry_fails_before_model_load(tmp_path):
    root=Path(__file__).parents[1]
    p=tmp_path/'manifest.jsonl'; p.write_text('{}\n')
    r=subprocess.run([sys.executable,str(root/'scripts/train_audio_semantic_m4_adapter_pilot.py'),'--manifest',str(p),'--steps','2'],cwd=root,text=True,capture_output=True)
    assert r.returncode!=0 and '尚未授权 gpu_2step' in r.stderr
