#!/usr/bin/env python
"""离线审计 audio generation review 输出，不加载模型。"""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20), b''): h.update(chunk)
    return h.hexdigest()

def norm(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument('--manifest',type=Path,required=True); ap.add_argument('--outputs',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    manifest=[json.loads(x) for x in args.manifest.read_text(encoding='utf-8').splitlines() if x.strip()]
    pred=[json.loads(x) for x in args.outputs.read_text(encoding='utf-8').splitlines() if x.strip()]
    expected={(r['pair_group_id'],r['condition']):r for r in manifest}
    actual={(r.get('pair_group_id'),r.get('condition')):r for r in pred}
    errors=[]
    if set(expected)!=set(actual): errors.append('prediction_key_set_mismatch')
    rows=[]
    for key,row in sorted(expected.items()):
        out=actual.get(key,{})
        text=str(out.get('prediction','')).strip()
        if not text: errors.append(f'empty_output:{key[0]}/{key[1]}')
        correct=norm(row['answer']) in norm(text) if text else False
        rows.append({'pair_group_id':key[0],'condition':key[1],'answer':row['answer'],'prediction':text,'normalized_exact_match':correct,'new_token_count':out.get('new_token_count')})
    by_group={}
    for row in rows: by_group.setdefault(row['pair_group_id'],{})[row['condition']]=row
    for gid,group in by_group.items():
        if set(group)!={'correct','mismatch','video_only','silence'}: errors.append(f'incomplete_output_group:{gid}'); continue
        if group['silence']['prediction'] != group['video_only']['prediction']:
            errors.append(f'silence_not_exact_video_only:{gid}')
    result={'status':'PASS_AUDIO_GENERATION_OUTPUT_AUDIT' if not errors else 'FAIL_AUDIO_GENERATION_OUTPUT_AUDIT','errors':errors,'manifest_sha256':sha256(args.manifest),'outputs_sha256':sha256(args.outputs),'records':len(rows),'groups':len(by_group),'accuracy_by_condition':{c:sum(r['normalized_exact_match'] for r in rows if r['condition']==c) for c in ('correct','mismatch','video_only','silence')}}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result,ensure_ascii=False,indent=2))
    if errors: raise SystemExit(1)
if __name__=='__main__': main()
