#!/usr/bin/env python
"""从已审计 AS-M4_STAGE2 dev 中构造 adapter-only 30 组四条件 manifest。"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--groups',type=int,default=30); args=ap.parse_args()
    rows=json.loads(args.input.read_text(encoding='utf-8'))
    audio=[r for r in rows if r.get('task_type')=='audio' and r.get('scene_audio_path') and r.get('event_label')]
    if len(audio)<args.groups*2: raise ValueError('audio-required 样本不足')
    chosen=audio[:args.groups]
    sources=[r for r in audio if r['event_label'] != chosen[0]['event_label']]
    out=[]
    for i,row in enumerate(chosen):
        mismatch=sources[i]
        base={k:row[k] for k in ('question','answer','event_label','video_path','video_id','physical_media_id','media_sha256','sample_id','task_type')}
        for condition, audio_row in [('video_only',None),('correct',row),('mismatch',mismatch),('silence',row)]:
            item=dict(base); item.update({'record_id':f"adapter_pilot_{i:02d}_{condition}",'pair_group_id':f"adapter_pilot_{i:02d}",'condition':condition,'audio_source_path':audio_row['scene_audio_path'] if audio_row else None,'audio_source_sha256':audio_row['audio_sha256'] if audio_row else None,'mismatch_source_sample_id':mismatch['sample_id'] if condition=='mismatch' else None,'router_target':1 if condition in {'correct','mismatch','silence'} else 0,'first_stage_training':True})
            out.append(item)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in out)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS_ADAPTER_PILOT_MANIFEST','groups':args.groups,'records':len(out),'input_sha256':sha(args.input),'output_sha256':sha(args.output)},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
