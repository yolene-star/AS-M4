#!/usr/bin/env python3
"""独立评估强时间原型 residual projector v2。"""
from __future__ import annotations
import argparse, hashlib, json
from collections import defaultdict
from pathlib import Path
import numpy as np, torch
from torch import nn
import torch.nn.functional as F

def sha256(p):
 h=hashlib.sha256(); f=open(p,'rb')
 for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
 f.close(); return h.hexdigest()
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
class ResidualProjector(nn.Module):
 def __init__(self):
  super().__init__(); self.down=nn.Linear(768,256); self.up=nn.Linear(256,768)
 def forward(self,x): return F.normalize(x.float()+0.25*self.up(F.gelu(self.down(x.float()))),dim=-1)
def pool(d):
 ts=d['timestamps'].float(); x=d['audio_embedding'].float(); out=[]
 for e in d['events']:
  w=(torch.minimum(ts[:,1],torch.tensor(float(e['offset'])))-torch.maximum(ts[:,0],torch.tensor(float(e['onset'])))).clamp_min(0)
  if w.sum()>0: out.append(((x*w[:,None]).sum(0)/w.sum(),e['event_label']))
 return out
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--feature-manifest',type=Path,required=True); ap.add_argument('--checkpoint',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
 ck=torch.load(args.checkpoint,map_location='cpu',weights_only=False); labels=ck['labels']; idx={l:i for i,l in enumerate(labels)}; model=ResidualProjector(); model.load_state_dict(ck['state_dict']); model.eval(); data=[]; train=[]; val=[]
 for r in rows(args.feature_manifest):
  d=torch.load(r['feature_path'],map_location='cpu',weights_only=False); items=pool(d); target=train if r['official_split']=='train' else val; target.append((r['video_id'],items))
 with torch.no_grad():
  tr=defaultdict(list)
  for _,items in train:
   for x,l in items: tr[l].append(model(x[None])[0])
  P=torch.stack([F.normalize(torch.stack(tr[l]).mean(0),dim=0) for l in labels]); per=defaultdict(list); margins=defaultdict(list); queries=[]
  for vid,items in val:
   for x,l in items:
    z=model(x[None])[0]; s=P@z; t=idx[l]; wrong=torch.cat((s[:t],s[t+1:])).max(); per[l].append(int(s.argmax())==t); margins[vid].append(float(s[t]-wrong)); queries.append((vid,l,z,float(s[t])))
  macro=float(np.mean([np.mean(v) for v in per.values()])); mm=float(np.mean([np.mean(v) for v in margins.values()])); mismatch={}
  for seed in (17,23,47):
   rng=np.random.default_rng(seed); dif=defaultdict(list)
   for vid,l,z,cs in queries:
    poolv=[(ov,oi) for ov,items in val if ov!=vid for oi,ol in items if ol!=l]
    ov,ox=poolv[int(rng.integers(len(poolv)))]; mz=model(ox[None])[0]; dif[vid].append(cs-float(P[idx[l]]@mz))
   vals=[np.mean(v) for v in dif.values()]; mismatch[str(seed)]={'mean_correct_minus_mismatch':float(np.mean(vals)),'positive':bool(np.mean(vals)>0)}
  result={'status':'PASS_TEMPORAL_PROJECTOR_V2' if macro>=0.21 and mm>0 and all(v['positive'] for v in mismatch.values()) else 'FAIL_TEMPORAL_PROJECTOR_V2','labels':len(labels),'validation_videos':len(val),'validation_queries':len(queries),'macro_recall_at_1':macro,'balanced_accuracy':macro,'mean_positive_minus_best_wrong_margin':mm,'mismatch':mismatch,'per_class_recall_at_1':{k:float(np.mean(v)) for k,v in per.items()},'checkpoint_sha256':sha256(args.checkpoint),'feature_manifest_sha256':sha256(args.feature_manifest),'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False}
 args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__': main()
