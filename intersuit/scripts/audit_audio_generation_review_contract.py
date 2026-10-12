#!/usr/bin/env python
"""审计 audio generation review 的单一来源、manifest SHA 与生成参数合同。"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''): h.update(c)
    return h.hexdigest()

def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument('--contract', type=Path, required=True); args = ap.parse_args()
    c = json.loads(args.contract.read_text(encoding='utf-8'))
    d = c['dataset_contract']; m = Path(d['manifest'])
    errors = []
    if not m.is_file(): errors.append('manifest_missing')
    else:
        if sha256(m) != d['manifest_sha256']: errors.append('manifest_sha_mismatch')
        rows = [json.loads(x) for x in m.read_text(encoding='utf-8').splitlines() if x.strip()]
        if len(rows) != d['records']: errors.append('record_count_mismatch')
        if {r.get('source_dataset') for r in rows} != {d['source_dataset']}: errors.append('mixed_source')
        if {r.get('split') for r in rows} != {d['source_split']}: errors.append('mixed_split')
        groups = {}
        for r in rows: groups.setdefault(r['pair_group_id'], {})[r['condition']] = r
        if len(groups) != d['groups']: errors.append('group_count_mismatch')
        for gid, g in groups.items():
            if set(g) != {'correct','mismatch','video_only','silence'}: errors.append(f'incomplete_group:{gid}'); continue
            if g['correct']['question'] != g['mismatch']['question'] or g['correct']['answer'] != g['mismatch']['answer']: errors.append(f'qa_mismatch:{gid}')
            if g['correct']['physical_media_id'] == g['mismatch']['mismatch_source_physical_media_id'] or g['correct']['event_label'].casefold() == str(g['mismatch']['mismatch_source_event_label']).casefold(): errors.append(f'invalid_mismatch:{gid}')
            if g['video_only']['audio_source_path'] is not None: errors.append(f'video_only_audio:{gid}')
    gen = c['generation']
    if gen != {'do_sample': False, 'temperature': 0.0, 'num_beams': 1, 'max_new_tokens': 64, 'same_prompt_across_conditions': True, 'same_video_across_conditions': True}: errors.append('generation_parameters_not_locked')
    model = c['model_contract']; checkpoint = Path(model['adapter_checkpoint'])
    if not checkpoint.is_file() or sha256(checkpoint) != model.get('adapter_checkpoint_sha256'):
        errors.append('adapter_checkpoint_sha_mismatch')
    if not (model.get('fusion_position') == 'llm_input_audio_tokens' and model.get('legacy_fusion_disabled') is True and model.get('audio_token_silence_bypass') is True and float(model.get('audio_token_silence_threshold', 0.0)) == 1e-6 and model.get('attn_implementation') == 'sdpa'):
        errors.append('adapter_runtime_configuration_not_locked')
    status = 'PASS_AUDIO_GENERATION_REVIEW_CONTRACT_AUDIT' if not errors else 'FAIL_AUDIO_GENERATION_REVIEW_CONTRACT_AUDIT'
    print(json.dumps({'status': status, 'errors': errors, 'contract_sha256': sha256(args.contract), 'manifest_sha256': sha256(m) if m.is_file() else None}, ensure_ascii=False, indent=2))
    if errors: raise SystemExit(1)

if __name__ == '__main__': main()
