#!/usr/bin/env python3
"""v4纯音频召回保持projector：统一projected prototype、真实hard negative、均衡采样与raw排序蒸馏。"""
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
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def vec(item):
 d=torch.load(item['feature_path'],map_location='cpu',weights_only=False); x=d['audio_embedding'].float(); w=torch.tensor(item['window_weights']); return (x*w[:,None]).sum(0)/w.sum()
class P(nn.Module):
 def __init__(self):
  super().__init__(); self.down=nn.Linear(768,256); self.up=nn.Linear(256,768); nn.init.zeros_(self.up.weight); nn.init.zeros_(self.up.bias)
 def forward(self,x): return F.normalize(x.float()+0.1*self.up(F.gelu(self.down(x.float()))),dim=-1)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=Path,required=True); ap.add_argument('--positive',type=Path,required=True); ap.add_argument('--negative',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--steps',type=int,required=True); args=ap.parse_args(); c=json.loads(args.contract.read_text());
 if sha256(args.positive)=='' or c['permissions']['beats_training_allowed'] or c['permissions']['m4_fusion_training_allowed']: raise PermissionError('合同错误')
 pos=rows(args.positive); neg=rows(args.negative); labels=sorted({x['event_label'] for x in pos}); idx={l:i for i,l in enumerate(labels)}; X=torch.stack([vec(x) for x in pos]); y=torch.tensor([idx[x['event_label']] for x in pos]); anchor_map=defaultdict(list); [anchor_map[(x['video_id'],x['event_label'])].append(vec(x)) for x in pos]
 model=P(); opt=torch.optim.AdamW(model.parameters(),lr=float(c['model']['lr'])); rng=random.Random(17); by_class=defaultdict(list)
 for i,label in enumerate(y.tolist()): by_class[label].append(i)
 history=[]
 for step in range(1,args.steps+1):
  classes=rng.sample(list(by_class),min(32,len(by_class))); sel=torch.tensor([rng.choice(by_class[k]) for k in classes for _ in range(8)]); z=model(X[sel]); proto=torch.stack([F.normalize(model(X[y==k]).mean(0),dim=0) for k in range(len(labels))]); logits=z@proto.T/float(c['model']['temperature']); target=y[sel]; ce=F.cross_entropy(logits,target); pos_score=logits.gather(1,target[:,None]).squeeze(1); hard=logits.masked_fill(F.one_hot(target,len(labels)).bool(),-1e9).max(1).values; hinge=F.relu(float(c['model']['margin'])-pos_score+hard).mean(); raw_proto=torch.stack([F.normalize(X[y==k].mean(0),dim=0) for k in range(len(labels))]); raw_logits=F.normalize(X[sel],dim=-1)@raw_proto.T/float(c['model']['temperature']); distill=F.kl_div(F.log_softmax(logits,dim=1),F.softmax(raw_logits.detach(),dim=1),reduction='batchmean'); identity=(1-(z*F.normalize(X[sel],dim=-1)).sum(1)).mean(); neg_batch=rng.sample(neg,min(512,len(neg))); anchors=torch.stack([random.choice(anchor_map[(n['anchor_video_id'],n['anchor_label'])]) for n in neg_batch]); negatives=torch.stack([vec(n['negative_event']) for n in neg_batch]); neg_z=model(negatives); anchor_z=model(anchors); neg_labels=torch.tensor([idx[n['anchor_label']] for n in neg_batch]); neg_pos=(anchor_z*proto[neg_labels]).sum(1); neg_score=(neg_z*proto[neg_labels]).sum(1); hardneg=F.relu(float(c['model']['margin'])-neg_pos+neg_score).mean(); loss=ce+float(c['model']['hard_negative_weight'])*(hinge+hardneg)+float(c['model']['identity_weight'])*identity+float(c['model']['distill_weight'])*distill; opt.zero_grad(); loss.backward(); opt.step(); history.append({'step':step,'loss':float(loss),'ce':float(ce),'prototype_hinge':float(hinge),'hardneg':float(hardneg),'identity':float(identity),'distill':float(distill),'classes_per_batch':len(classes)})
 args.output.parent.mkdir(parents=True,exist_ok=True); torch.save({'state_dict':model.state_dict(),'labels':labels,'training_performed':True,'beats_training_performed':False,'m4_fusion_training_performed':False},args.output.with_suffix('.pt')); args.output.write_text(json.dumps({'status':'PASS_AUDIO_PROJECTOR_V4_TRAINING','steps':args.steps,'train_positive':len(pos),'train_hard_negative':len(neg),'classes':len(labels),'final':history[-1],'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False,'history':history},ensure_ascii=False,indent=2)+'\n'); print(json.dumps({'status':'PASS_AUDIO_PROJECTOR_V4_TRAINING','steps':args.steps,'final':history[-1]},ensure_ascii=False))
if __name__=='__main__': main()
