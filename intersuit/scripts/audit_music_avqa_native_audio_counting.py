#!/usr/bin/env python
"""审计 MUSIC-AVQA 原生 Audio/Counting smoke manifest。"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
from new_dataset_common import replace_music_template

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''):h.update(c)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--annotations',type=Path,default=Path('intersuit/datasets/NEW_AV_TRAIN/quarantine/metadata/train_balance.json'));ap.add_argument('--pilot-manifest',type=Path,default=Path('intersuit/harness/artifacts/audio_semantic_m4_adapter_only_pilot_v1/manifest.jsonl'));args=ap.parse_args();rows=[json.loads(x) for x in args.manifest.read_text().splitlines() if x.strip()];errors=[];groups={}
    annotations=json.loads(args.annotations.read_text(encoding='utf-8'))
    native=set()
    for row in annotations:
        if int(row.get('question_deleted',0) or 0)!=0:continue
        raw_type=row.get('type');raw_type=json.loads(raw_type) if isinstance(raw_type,str) else raw_type
        if raw_type!=['Audio','Counting']:continue
        values=row.get('templ_values') or [];values=json.loads(values) if isinstance(values,str) else values
        question=replace_music_template(row.get('question_content',''),list(values)).strip()
        native.add((str(row.get('video_id')),question,str(row.get('anser','')).strip()))
    pilot_media={json.loads(x)['physical_media_id'] for x in args.pilot_manifest.read_text().splitlines() if x.strip()}
    for r in rows:groups.setdefault(r['pair_group_id'],{})[r['condition']]=r
    if len(groups)!=2 or len(rows)!=8:errors.append('size_mismatch')
    for gid,g in groups.items():
        if set(g)!={'correct','mismatch','video_only','silence'}:errors.append(f'incomplete:{gid}');continue
        if {r['source_dataset'] for r in g.values()}!={'MUSIC-AVQA-v2.0'} or {r['split'] for r in g.values()}!={'dev'}:errors.append(f'source_split:{gid}')
        if len({r['question'] for r in g.values()})!=1 or len({r['answer'] for r in g.values()})!=1:errors.append(f'qa_drift:{gid}')
        if g['mismatch']['mismatch_source_answer']==g['correct']['answer'] or g['mismatch']['mismatch_source_physical_media_id']==g['correct']['physical_media_id']:errors.append(f'invalid_mismatch:{gid}')
        if g['correct']['physical_media_id'] in pilot_media or g['mismatch']['mismatch_source_physical_media_id'] in pilot_media:errors.append(f'pilot_overlap:{gid}')
        if (str(g['correct']['video_id']).removesuffix('_flip'),g['correct']['question'],g['correct']['answer']) not in native:errors.append(f'native_target_missing:{gid}')
        if (str(g['mismatch']['mismatch_source_video_id']).removesuffix('_flip'),g['mismatch']['question'],g['mismatch']['mismatch_source_answer']) not in native:errors.append(f'native_mismatch_missing:{gid}')
        if g['video_only']['audio_source_path'] is not None:errors.append(f'video_only_audio:{gid}')
        for condition in ('correct','mismatch','silence'):
            p=Path(g[condition]['audio_source_path'])
            if not p.is_file() or sha256(p)!=g[condition]['audio_source_sha256']:errors.append(f'audio_sha:{gid}/{condition}')
    print(json.dumps({'status':'PASS_MUSIC_AVQA_NATIVE_COUNTING_AUDIT' if not errors else 'FAIL_MUSIC_AVQA_NATIVE_COUNTING_AUDIT','errors':errors,'groups':len(groups),'records':len(rows),'manifest_sha256':sha256(args.manifest),'annotations_sha256':sha256(args.annotations),'native_audio_counting_rows':len(native)},ensure_ascii=False,indent=2))
    if errors:raise SystemExit(1)
if __name__=='__main__':main()
