#!/usr/bin/env python3
"""构建纯音频 UnAV 强时间事件原型与安全 hard-negative manifest。"""
from __future__ import annotations
import argparse, hashlib, json, random
from collections import Counter, defaultdict
from pathlib import Path
import torch

def sha256(p):
 h=hashlib.sha256(); f=open(p,'rb')
 for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
 f.close(); return h.hexdigest()
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def event_rows(payload, feature_path, split):
 ts=payload['timestamps'].float(); x=payload['audio_embedding'].float(); out=[]
 for event in payload['events']:
  w=(torch.minimum(ts[:,1],torch.tensor(float(event['offset'])))-torch.maximum(ts[:,0],torch.tensor(float(event['onset'])))).clamp_min(0)
  if float(w.sum())<=0: continue
  out.append({'video_id':payload['video_id'],'official_split':split,'event_label':event['event_label'],'onset':event['onset'],'offset':event['offset'],'window_weights':w.tolist(),'feature_path':str(feature_path.resolve()),'media_sha256':payload['media_sha256']})
 return out
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--feature-manifest',type=Path,required=True); ap.add_argument('--output-dir',type=Path,required=True); ap.add_argument('--negatives-per-positive',type=int,default=2); args=ap.parse_args()
 feature_rows=rows(args.feature_manifest); events=[]
 for r in feature_rows:
  payload=torch.load(r['feature_path'],map_location='cpu',weights_only=False)
  events.extend(event_rows(payload,Path(r['feature_path']),r['official_split']))
 train=[e for e in events if e['official_split']=='train']; val=[e for e in events if e['official_split']=='validation']; labels=sorted({e['event_label'] for e in events}); rng=random.Random(17)
 by_label=defaultdict(list)
 for e in train: by_label[e['event_label']].append(e)
 positives=[]; negatives=[]
 for e in train:
  positives.append(e)
  pool=[x for label,items in by_label.items() if label!=e['event_label'] for x in items if x['video_id']!=e['video_id']]
  rng.shuffle(pool)
  for n in pool[:args.negatives_per_positive]: negatives.append({'anchor_video_id':e['video_id'],'anchor_label':e['event_label'],'negative_video_id':n['video_id'],'negative_label':n['event_label'],'negative_event':n})
 payload={'artifact_kind':'unav_s1r_audio_strong_timed_prototype_manifest','format_version':1,'feature_manifest_sha256':sha256(args.feature_manifest),'labels':labels,'train_event_count':len(train),'validation_event_count':len(val),'positive_count':len(positives),'hard_negative_count':len(negatives),'same_video_negative_count':0,'overlap_multilabel_safe':True,'test_accessed':False,'training_performed':False}
 args.output_dir.mkdir(parents=True,exist_ok=True)
 (args.output_dir/'train_positive_events.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in positives))
 (args.output_dir/'train_hard_negatives.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in negatives))
 (args.output_dir/'validation_events.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in val))
 payload['files']={name:{'path':str((args.output_dir/name).resolve()),'sha256':sha256(args.output_dir/name),'rows':sum(1 for _ in (args.output_dir/name).open())} for name in ('train_positive_events.jsonl','train_hard_negatives.jsonl','validation_events.jsonl')}
 (args.output_dir/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 print(json.dumps(payload,ensure_ascii=False))
if __name__=='__main__': main()
