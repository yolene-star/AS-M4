#!/usr/bin/env python
"""构建 MUSIC-AVQA dev 原生 Audio/Counting 两组反事实 smoke manifest。"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

QUESTION = "How many musical instruments were heard throughout the video?"
CONDITIONS = ("correct", "mismatch", "video_only", "silence")

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,default=Path('intersuit/datasets/AS_M4_STAGE2/dev_manifest.json')); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    rows=json.loads(args.input.read_text(encoding='utf-8'))
    pool=[r for r in rows if r.get('source_dataset')=='MUSIC-AVQA-v2.0' and r.get('split')=='dev' and r.get('task_type')=='audio' and r.get('question','').strip()==QUESTION]
    targets=[]
    for answer in ('three','more than ten'):
        candidates=sorted([r for r in pool if r.get('answer')==answer],key=lambda r:r['sample_id'])
        if len(candidates)!=1: raise ValueError(f'目标答案 {answer} 候选应恰为 1，实际={len(candidates)}')
        targets.append(candidates[0])
    fours=sorted([r for r in pool if r.get('answer')=='four'],key=lambda r:r['sample_id'])
    if len(fours)<2: raise ValueError('four mismatch 来源不足')
    output=[]
    for index,(target,mismatch) in enumerate(zip(targets,fours)):
        if target['physical_media_id']==mismatch['physical_media_id']: raise ValueError('mismatch physical media 相同')
        base={k:target[k] for k in ('question','answer','video_path','video_id','physical_media_id','media_sha256','sample_id','task_type','source_dataset','split','source_revision')}
        gid=f'music_native_counting_{index:02d}'
        for condition in CONDITIONS:
            source=target if condition in {'correct','silence'} else (mismatch if condition=='mismatch' else None)
            item=dict(base); item.update({'record_id':f'{gid}_{condition}','pair_group_id':gid,'condition':condition,'native_subtype':'Counting','audio_source_path':source['scene_audio_path'] if source else None,'audio_source_sha256':source['audio_sha256'] if source else None,'mismatch_source_sample_id':mismatch['sample_id'] if condition=='mismatch' else None,'mismatch_source_video_id':mismatch['video_id'] if condition=='mismatch' else None,'mismatch_source_physical_media_id':mismatch['physical_media_id'] if condition=='mismatch' else None,'mismatch_source_answer':mismatch['answer'] if condition=='mismatch' else None,'first_stage_training':False})
            output.append(item)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in output),encoding='utf-8')
    print(json.dumps({'status':'PASS_MUSIC_AVQA_NATIVE_COUNTING_BUILD','groups':2,'records':8,'input_sha256':sha256(args.input),'output_sha256':sha256(args.output),'target_answers':[r['answer'] for r in targets],'mismatch_answers':[r['answer'] for r in fours[:2]]},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
