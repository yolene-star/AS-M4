#!/usr/bin/env python
"""运行锁定 AVE audio generation review 的小范围 GPU smoke。"""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPO_ROOT=ROOT.parent

def repo_path(value: str) -> Path:
    path=Path(value)
    return path.resolve() if path.is_absolute() else (REPO_ROOT/path).resolve()

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--contract',type=Path,required=True); ap.add_argument('--limit-groups',type=int,default=2)
    ap.add_argument('--device',default='cuda:0'); ap.add_argument('--output-dir',type=Path,required=True)
    args=ap.parse_args(); contract=json.loads(args.contract.read_text(encoding='utf-8'))
    if contract['authorization'].get('gpu_generation_smoke') is not True: raise PermissionError('generation smoke 尚未授权；停止，不加载 M4/GPU')
    if args.device!='cuda:0' or args.limit_groups!=2: raise ValueError('本合同只允许逻辑 cuda:0 和 2 组 smoke')
    if args.output_dir.exists() and any(args.output_dir.iterdir()): raise FileExistsError(f'输出目录非空：{args.output_dir}')
    import torch
    from intersuit.train.audio_semantic_m4_adapter import adapter_frozen_runtime_sha256, adapter_state_sha256, group_pilot_manifest, load_adapter_only_checkpoint, load_adapter_only_model, load_pilot_manifest, prepare_real_generation_batch
    from intersuit.harness.runners.run_predictions_from_plan import decode_generated_tokens, extract_generated_token_ids
    manifest=repo_path(contract['dataset_contract']['manifest']); checkpoint=repo_path(contract['model_contract']['adapter_checkpoint'])
    if sha256(manifest)!=contract['dataset_contract']['manifest_sha256']: raise ValueError('manifest SHA 不一致')
    if sha256(checkpoint)!=contract['model_contract']['adapter_checkpoint_sha256']: raise ValueError('adapter checkpoint SHA 不一致')
    groups=group_pilot_manifest(load_pilot_manifest(manifest))[:args.limit_groups]
    args.output_dir.mkdir(parents=True); model_contract=contract['model_contract']; tokenizer,model,image_processor,_ctx,names=load_adapter_only_model({
        'base_model': {'checkpoint':model_contract['base_checkpoint'],'m4_hidden_size':3584,
                       'fusion_position':model_contract['fusion_position'],'audio_token_gate':model_contract['audio_token_gate'],
                       'audio_token_silence_bypass':model_contract['audio_token_silence_bypass'],
                       'audio_token_silence_threshold':model_contract['audio_token_silence_threshold'],
                       'attn_implementation':model_contract['attn_implementation']},
        'audio_source': {'checkpoint':'intersuit/harness/artifacts/unav_s1r_temporal_projector_v2/summary.pt','sha256':'0bf779dbc1678328d935089f8d41c16e216266b694dd86e51415bfb96f92a610'}},device=args.device)
    payload=load_adapter_only_checkpoint(checkpoint,model)
    model.eval(); frozen_before=adapter_frozen_runtime_sha256(model); adapter_before=adapter_state_sha256(model); torch.cuda.reset_peak_memory_stats(torch.device(args.device)); started=time.perf_counter(); rows=[]
    with torch.inference_mode():
        for group in groups:
            cache={}
            for condition in ('correct','mismatch','video_only','silence'):
                batch=prepare_real_generation_batch(group['conditions'][condition],tokenizer,model,image_processor,device=args.device,video_cache=cache)
                prompt_ids=batch.pop('input_ids')
                out=model.generate(prompt_ids,**batch,do_sample=False,temperature=0.0,num_beams=1,max_new_tokens=64,use_cache=True,pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
                tokens=extract_generated_token_ids(out,prompt_ids,'generate'); prediction,debug=decode_generated_tokens(tokenizer,tokens)
                rows.append({'pair_group_id':group['pair_group_id'],'condition':condition,'prediction':prediction,**debug})
    if adapter_frozen_runtime_sha256(model)!=frozen_before or adapter_state_sha256(model)!=adapter_before: raise RuntimeError('generation 发生权重变化')
    rows_path=args.output_dir/'outputs.jsonl'; rows_path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    summary={'status':'PASS_AUDIO_GENERATION_SMOKE','contract_sha256':sha256(args.contract),'manifest_sha256':sha256(manifest),'checkpoint_sha256':sha256(checkpoint),'checkpoint_metadata':payload['metadata'],'trainable_names':list(names),'rows_sha256':sha256(rows_path),'groups':len(groups),'records':len(rows),'runtime_seconds':time.perf_counter()-started,'cuda_peak_memory_allocated_bytes':int(torch.cuda.max_memory_allocated(torch.device(args.device))),'frozen_runtime_sha256':frozen_before,'adapter_sha256':adapter_before}
    (args.output_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
