#!/usr/bin/env python
"""将 adapter pilot 四条件 manifest 转换为 M4 对话样本格式。"""
from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    rows=[json.loads(x) for x in args.input.read_text(encoding='utf-8').splitlines() if x]
    out=[]
    for r in rows:
        item={
            'id':r['record_id'],'sample_id':r['record_id'],'pair_group_id':r['pair_group_id'],
            'condition':r['condition'],'task_type':r['task_type'],'event_label':r['event_label'],
            'question':r['question'],'answer':r['answer'],'video_path':r['video_path'],
            'physical_media_id':r['physical_media_id'],'media_sha256':r['media_sha256'],
            'audio_source_path':r['audio_source_path'],'audio_source_sha256':r['audio_source_sha256'],
            'mismatch_source_sample_id':r.get('mismatch_source_sample_id'),
            'conversations':[{'from':'human','value':'<image>\n'+r['question']},{'from':'gpt','value':r['answer']}],
        }
        if r['condition'] in {'correct','mismatch','silence'}: item['scene_audio_path']=r['audio_source_path']
        out.append(item)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'status':'PASS_ADAPTER_PILOT_M4_CONVERSION','records':len(out),'groups':len({r["pair_group_id"] for r in rows})},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
