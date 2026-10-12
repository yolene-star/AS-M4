#!/usr/bin/env python
"""独立审计 adapter-only pilot manifest 的音频来源与四条件合同。"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--manifest',type=Path,required=True); args=ap.parse_args()
    rows=[json.loads(x) for x in args.manifest.read_text(encoding='utf-8').splitlines() if x]
    groups={}
    for r in rows: groups.setdefault(r['pair_group_id'],{})[r['condition']]=r
    required={'video_only','correct','mismatch','silence'}
    checked={}
    for gid,g in groups.items():
        if set(g)!=required: raise ValueError(f'{gid} 四条件不完整')
        if g['video_only']['audio_source_path'] is not None: raise ValueError('video_only 不得有音频')
        for c in ('correct','mismatch','silence'):
            p=Path(g[c]['audio_source_path'])
            if not p.is_file() or sha(p)!=g[c]['audio_source_sha256']: raise ValueError(f'{gid}/{c} 音频 SHA 漂移')
            checked[str(p)]=g[c]['audio_source_sha256']
        if g['correct']['audio_source_sha256']==g['mismatch']['audio_source_sha256']: raise ValueError('correct/mismatch 音频相同')
        if g['correct']['question']!=g['mismatch']['question'] or g['correct']['answer']!=g['mismatch']['answer']: raise ValueError('pair 问答漂移')
    result={'status':'PASS_ADAPTER_PILOT_MANIFEST_AUDIT','records':len(rows),'groups':len(groups),'unique_audio_files':len(checked),'manifest_sha256':sha(args.manifest),'silence_materialization':'zero_like_correct_waveform_required'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
