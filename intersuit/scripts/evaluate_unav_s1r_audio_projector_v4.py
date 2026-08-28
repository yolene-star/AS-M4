#!/usr/bin/env python3
"""在同一query集合上比较raw BEATs与v4 projector。"""
from __future__ import annotations
import argparse,json
from collections import defaultdict
from pathlib import Path
import numpy as np,torch
from torch import nn
import torch.nn.functional as F
class P(nn.Module):
 def __init__(self): super().__init__(); self.down=nn.Linear(768,256); self.up=nn.Linear(256,768)
 def forward(self,x): return F.normalize(x.float()+0.1*self.up(F.gelu(self.down(x.float()))),dim=-1)
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def pool(d):
 ts=d['timestamps'].float(); x=d['audio_embedding'].float(); out=[]
 for e in d['events']:
  w=(torch.minimum(ts[:,1],torch.tensor(float(e['offset'])))-torch.maximum(ts[:,0],torch.tensor(float(e['onset'])))).clamp_min(0)
  if w.sum()>0: out.append(((x*w[:,None]).sum(0)/w.sum(),e['event_label']))
 return out
def score(train,val,labels,transform):
 idx={l:i for i,l in enumerate(labels)}; tr=defaultdict(list)
 for _,items in train:
  for x,l in items: tr[l].append(transform(x))
 Pm=torch.stack([F.normalize(torch.stack(tr[l]).mean(0),dim=0) for l in labels]); per=defaultdict(list); margins=defaultdict(list); correct_raw=[]
 for vid,items in val:
  for x,l in items:
   z=transform(x); s=Pm@z; t=idx[l]; wrong=torch.cat((s[:t],s[t+1:])).max(); ok=int(s.argmax())==t; per[l].append(ok); margins[vid].append(float(s[t]-wrong)); correct_raw.append((vid,l,ok))
 return {'macro_recall_at_1':float(np.mean([np.mean(v) for v in per.values()])), 'mean_margin':float(np.mean([np.mean(v) for v in margins.values()])), 'per_class':{k:float(np.mean(v)) for k,v in per.items()},'decisions':correct_raw}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--feature-manifest',type=Path,required=True); ap.add_argument('--checkpoint',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args(); ck=torch.load(a.checkpoint,map_location='cpu',weights_only=False); labels=ck['labels']; model=P(); model.load_state_dict(ck['state_dict']); model.eval(); train=[]; val=[]
 for r in rows(a.feature_manifest):
  d=torch.load(r['feature_path'],map_location='cpu',weights_only=False); (train if r['official_split']=='train' else val).append((r['video_id'],pool(d)))
 with torch.no_grad(): raw=score(train,val,labels,lambda x:F.normalize(x.float(),dim=0)); projected=score(train,val,labels,lambda x:model(x[None])[0])
 rd={(v,l):ok for v,l,ok in raw.pop('decisions')}; pd={(v,l):ok for v,l,ok in projected.pop('decisions')}; keys=set(rd)&set(pd); preserved=sum(rd[k] and pd[k] for k in keys); raw_correct=sum(rd[k] for k in keys)
 result={'status':'PASS_V4_UNIFIED_EVAL' if projected['mean_margin']>0 and projected['macro_recall_at_1']>=raw['macro_recall_at_1']-0.02 else 'FAIL_V4_UNIFIED_EVAL','validation_videos':len(val),'validation_queries':len(keys),'raw_beats':raw,'projector_v4':projected,'macro_delta':projected['macro_recall_at_1']-raw['macro_recall_at_1'],'margin_delta':projected['mean_margin']-raw['mean_margin'],'raw_correct_preservation_rate':preserved/raw_correct if raw_correct else None,'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False}
 a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); print(json.dumps({k:result[k] for k in ['status','validation_queries','raw_beats','projector_v4','macro_delta','margin_delta','raw_correct_preservation_rate'] if k in result},ensure_ascii=False))
if __name__=='__main__': main()
