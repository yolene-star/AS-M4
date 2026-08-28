#!/usr/bin/env python3
"""独立复评已训练的 UnAV 语义 projector；不训练、不接入 M4。"""
from __future__ import annotations
import argparse, hashlib, json, random
from collections import defaultdict
from pathlib import Path
import numpy as np
import torch
from torch import nn

def sha256(p):
 h=hashlib.sha256(); f=open(p,'rb')
 for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
 f.close(); return h.hexdigest()
def read_jsonl(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def events(d):
 ts=d['timestamps'].float(); x=d['audio_embedding'].float(); out=[]
 for e in d['events']:
  w=(torch.minimum(ts[:,1],torch.tensor(float(e['offset'])))-torch.maximum(ts[:,0],torch.tensor(float(e['onset'])))).clamp_min(0)
  if w.sum()>0: out.append((video:=((x*w[:,None]).sum(0)/w.sum()),e['event_label']))
 return out
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--feature-manifest',type=Path,required=True); ap.add_argument('--checkpoint',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
 ck=torch.load(args.checkpoint,map_location='cpu',weights_only=False); labels=ck['labels']; idx={x:i for i,x in enumerate(labels)}
 model=nn.Sequential(nn.Linear(768,128),nn.LayerNorm(128),nn.GELU(),nn.Linear(128,len(labels))); model.load_state_dict(ck['state_dict']); model.eval()
 rows=read_jsonl(args.feature_manifest); val=[]
 for r in rows:
  if r['official_split']!='validation': continue
  d=torch.load(r['feature_path'],map_location='cpu',weights_only=False)
  items=events(d); val.append((r['video_id'],{label for _,label in items},items))
 queries=[]; by_label=defaultdict(list)
 for vid,video_labels,items in val:
  for vec,label in items:
   if label not in idx: continue
   queries.append((vid,label,vec)); by_label[label].append((vid,vec))
 per_class=defaultdict(list); margin_by_video=defaultdict(list); mismatch_by_seed={}
 with torch.no_grad():
  for vid,label,vec in queries:
   scores=model(vec[None]).squeeze(0); target=idx[label]; wrong=torch.cat((scores[:target],scores[target+1:])).max(); per_class[label].append(int(scores.argmax())==target); margin_by_video[vid].append(float(scores[target]-wrong))
  for seed in (17,23,47):
   rng=random.Random(seed); diffs=defaultdict(list)
   for vid,label,vec in queries:
    pool=[(other_vid,other_vec) for other_vid,other_labels,other_items in val if other_vid!=vid and label not in other_labels for other_vec,other_label in other_items if other_label!=label]
    if not pool: continue
    ov, ovec=rng.choice(pool); target=idx[label]; correct=float(model(vec[None])[0,target]); mism=float(model(ovec[None])[0,target]); diffs[vid].append(correct-mism)
   values=[float(np.mean(v)) for v in diffs.values() if v]; mismatch_by_seed[str(seed)]={'mean_correct_minus_mismatch':float(np.mean(values)),'positive':bool(values and np.mean(values)>0),'videos':len(values)}
 per={k:float(np.mean(v)) for k,v in per_class.items()}; macro=float(np.mean(list(per.values())))
 margins=[float(np.mean(v)) for v in margin_by_video.values()]; mean_margin=float(np.mean(margins))
 result={'status':'PASS_PROJECTOR_SEMANTIC_REEVAL' if macro>0.21 and mean_margin>0 and all(v['positive'] for v in mismatch_by_seed.values()) else 'FAIL_PROJECTOR_SEMANTIC_REEVAL','labels':len(labels),'validation_videos':len(val),'validation_queries':len(queries),'macro_recall_at_1':macro,'balanced_accuracy':macro,'mean_positive_minus_best_wrong_margin':mean_margin,'mismatch':mismatch_by_seed,'per_class_recall_at_1':per,'checkpoint_sha256':sha256(args.checkpoint),'feature_manifest_sha256':sha256(args.feature_manifest),'training_performed':False,'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False}
 args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__': main()
