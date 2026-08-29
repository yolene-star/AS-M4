#!/usr/bin/env python3
"""审计新 fusion projector 三条件真实7B gate0 smoke。"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def one(path: Path) -> dict:
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x]
    if len(rows) != 1: raise ValueError(f'{path} must contain one row')
    return rows[0]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args(); root = a.root
    files = {k: root / f for k, f in {'behavior':'v4_behavior_off_predictions.jsonl','bypass':'v4_bypass_gate0_predictions.jsonl','lightweight':'v4_lightweight_gate0_predictions.jsonl'}.items()}
    rows = {k: one(p) for k,p in files.items()}
    toks = {k: rows[k]['generation_debug']['tokens'] for k in rows}
    checks = {
        'same_sample': len({r['sample_id'] for r in rows.values()}) == 1,
        'prediction_exact_match': len({r['prediction'] for r in rows.values()}) == 1,
        'tokens_exact_match': len({tuple(t['first_20_new_token_ids']) for t in toks.values()}) == 1,
        'bypass_mode_active': toks['bypass']['as_m4_fusion_projector_mode'] == 'bypass',
        'lightweight_mode_active': toks['lightweight']['as_m4_fusion_projector_mode'] == 'lightweight',
        'gate0_exact_zero': all(rows[k]['gate'] == 0.0 and rows[k]['delta_to_video_ratio'] == 0.0 for k in ('bypass','lightweight')),
        'audio_decoded': all(float(rows[k]['audio_norm']) > 0 for k in ('bypass','lightweight')),
        'no_first_token_eos': all(not t['first_token_is_eos'] for t in toks.values()),
    }
    result = {'status':'PASS_FUSION_PROJECTOR_GATE0_EXACT_ROLLBACK' if all(checks.values()) else 'FAIL_FUSION_PROJECTOR_GATE0', 'checks':checks, 'sample_id':rows['behavior']['sample_id'], 'predictions':{k:rows[k]['prediction'] for k in rows}, 'modes':{k:toks[k]['as_m4_fusion_projector_mode'] for k in toks}, 'artifact_sha256':{k:sha(p) for k,p in files.items()}, 'training_performed':False, 'nonzero_gate_executed':False, 'formal_test_accessed':False, 'm4_effect_claim_allowed':False}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8'); print(json.dumps(result,ensure_ascii=False))
    if not result['status'].startswith('PASS'): raise SystemExit(1)
if __name__ == '__main__': main()
