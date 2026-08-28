#!/usr/bin/env python3
"""使用 UnAV onset/offset 事件原型训练保几何 residual semantic projector。"""
from __future__ import annotations
import argparse, hashlib, json, random
from collections import defaultdict
from pathlib import Path
import torch
from torch import nn
import torch.nn.functional as F

def sha256(p):
 h=hashlib.sha256(); f=open(p,'rb')
 for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
 f.close(); return h.hexdigest()
def read_jsonl(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def pool_events(d):
 ts=d['timestamps'].float(); x=d['audio_embedding'].float(); out=[]
 for e in d['events']:
  w=(torch.minimum(ts[:,1],torch.tensor(float(e['offset'])))-torch.maximum(ts[:,0],torch.tensor(float(e['onset'])))).clamp_min(0)
  if w.sum()>0: out.append(((x*w[:,None]).sum(0)/w.sum(),e['event_label']))
 return out
class ResidualProjector(nn.Module):
 def __init__(self):
  super().__init__(); self.down=nn.Linear(768,256); self.up=nn.Linear(256,768); nn.init.zeros_(self.up.weight); nn.init.zeros_(self.up.bias)
 def forward(self,x): return F.normalize(x.float()+0.25*self.up(F.gelu(self.down(x.float()))),dim=-1)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=Path,required=True); ap.add_argument('--feature-manifest',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
 c=json.loads(args.contract.read_text()); p=c['permissions'];
 if p['standalone_projector_training_allowed'] is not True or p['beats_training_allowed'] is not False or p['m4_fusion_training_allowed'] is not False: raise PermissionError('合同不匹配')
 if sha256(args.feature_manifest)!=c['feature_manifest_sha256']: raise ValueError('manifest SHA漂移')
 rows=read_jsonl(args.feature_manifest); train=[]; labels=sorted({e['event_label'] for r in rows if r['official_split']=='train' for e in json.loads(Path(r['feature_path']).read_bytes()[:0] or b'{}').get('events',[])})
 all_items=[]
 for r in rows:
  d=torch.load(r['feature_path'],map_location='cpu',weights_only=False); items=pool_events(d); all_items.extend([(r['official_split'],x,l) for x,l in items])
 labels=sorted({l for _,_,l in all_items}); idx={l:i for i,l in enumerate(labels)}; train=[(x,l) for s,x,l in all_items if s=='train']
 X=torch.stack([x for x,_ in train]); y=torch.tensor([idx[l] for _,l in train]); protos=[]
 for label in labels: protos.append(F.normalize(X[y==idx[label]].mean(0),dim=0))
 P=torch.stack(protos); model=ResidualProjector(); opt=torch.optim.AdamW(model.parameters(),lr=float(c['model']['lr'])); rng=random.Random(17); history=[]
 for step in range(1,int(c['model']['steps'])+1):
  sel=torch.tensor([rng.randrange(len(X)) for _ in range(min(1024,len(X)))])
  z=model(X[sel]); sim=z@P.T/float(c['model']['prototype_temperature']); target=y[sel]; ce=F.cross_entropy(sim,target); pos=sim.gather(1,target[:,None]).squeeze(1); mask=F.one_hot(target,num_classes=len(labels)).bool(); hard=sim.masked_fill(mask,-1e9).max(1).values; hinge=F.relu(float(c['model']['margin'])-(pos-hard)).mean(); preserve=(1-(z*F.normalize(X[sel],dim=-1)).sum(1)).mean(); loss=ce+hinge+float(c['model']['preserve_weight'])*preserve; opt.zero_grad(); loss.backward(); opt.step(); history.append({'step':step,'loss':float(loss),'ce':float(ce),'hinge':float(hinge),'preserve':float(preserve)})
 args.output.parent.mkdir(parents=True,exist_ok=True); torch.save({'state_dict':model.state_dict(),'labels':labels,'training_performed':True,'beats_training_performed':False,'objective':c['objective']},args.output.with_suffix('.pt')); args.output.write_text(json.dumps({'status':'PASS_TEMPORAL_PROTOTYPE_PROJECTOR_TRAINING','train_items':len(train),'classes':len(labels),'steps':len(history),'final':history[-1],'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False,'history':history},ensure_ascii=False,indent=2)+'\n'); print(json.dumps({'status':'PASS_TEMPORAL_PROTOTYPE_PROJECTOR_TRAINING','train_items':len(train),'classes':len(labels),'final':history[-1]},ensure_ascii=False))
if __name__=='__main__': main()
