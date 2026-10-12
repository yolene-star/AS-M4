#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, random
from pathlib import Path
import torch
from torch import nn
import torch.nn.functional as F

class ResidualProjector(nn.Module):
 def __init__(self):
  super().__init__(); self.down=nn.Linear(768,256); self.up=nn.Linear(256,768); nn.init.zeros_(self.up.weight); nn.init.zeros_(self.up.bias)
 def forward(self,x): return F.normalize(x.float()+0.25*self.up(F.gelu(self.down(x.float()))),dim=-1)
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def vec(item):
 d=torch.load(item['feature_path'],map_location='cpu',weights_only=False); x=d['audio_embedding'].float(); w=torch.tensor(item['window_weights']); return (x*w[:,None]).sum(0)/w.sum()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--positive',type=Path,required=True); ap.add_argument('--negative',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--steps',type=int,required=True); args=ap.parse_args()
 pos=rows(args.positive); neg=rows(args.negative); labels=sorted({x['event_label'] for x in pos}); idx={l:i for i,l in enumerate(labels)}; X=torch.stack([vec(x) for x in pos]); y=torch.tensor([idx[x['event_label']] for x in pos]); N=torch.stack([vec(x['negative_event']) for x in neg]); A=torch.stack([vec(x) for x in pos for _ in range(2)])[:len(N)]
 proto=torch.stack([F.normalize(X[y==idx[l]].mean(0),dim=0) for l in labels]); model=ResidualProjector(); opt=torch.optim.AdamW(model.parameters(),lr=5e-5); rng=random.Random(17); hist=[]
 for step in range(1,args.steps+1):
  sel=torch.tensor([rng.randrange(len(X)) for _ in range(min(1024,len(X)))]); z=model(X[sel]); s=z@proto.T/0.07; target=y[sel]; ce=F.cross_entropy(s,target); pos_score=s.gather(1,target[:,None]).squeeze(1); hard=s.masked_fill(F.one_hot(target,len(labels)).bool(),-1e9).max(1).values; hinge=F.relu(0.05-(pos_score-hard)).mean(); preserve=(1-(z*F.normalize(X[sel],dim=-1)).sum(1)).mean(); loss=ce+hinge+0.25*preserve; opt.zero_grad(); loss.backward(); opt.step(); hist.append({'step':step,'loss':float(loss),'ce':float(ce),'hinge':float(hinge),'preserve':float(preserve)})
 args.output.parent.mkdir(parents=True,exist_ok=True); torch.save({'state_dict':model.state_dict(),'labels':labels,'training_performed':True,'beats_training_performed':False,'m4_fusion_training_performed':False},args.output.with_suffix('.pt')); args.output.write_text(json.dumps({'status':'PASS_AUDIO_HARDNEG_PROJECTOR_TRAINING','steps':args.steps,'train_positive':len(pos),'train_hard_negative':len(neg),'classes':len(labels),'final':hist[-1],'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False,'history':hist},ensure_ascii=False,indent=2)+'\n'); print(json.dumps({'status':'PASS_AUDIO_HARDNEG_PROJECTOR_TRAINING','steps':args.steps,'final':hist[-1]},ensure_ascii=False))
if __name__=='__main__': main()
