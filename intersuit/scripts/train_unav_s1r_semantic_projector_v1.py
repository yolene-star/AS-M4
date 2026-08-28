#!/usr/bin/env python3
"""训练独立UnAV事件语义projector；不训练BEATs，不接入M4。"""
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
def event_windows(d):
 ts=d['timestamps'].float(); x=d['audio_embedding'].float(); out=[]
 for e in d['events']:
  w=(torch.minimum(ts[:,1],torch.tensor(float(e['offset'])))-torch.maximum(ts[:,0],torch.tensor(float(e['onset'])))).clamp_min(0)
  if w.sum()>0: out.append(((x*w[:,None]).sum(0)/w.sum(),e['event_label']))
 return out
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=Path,required=True); ap.add_argument('--feature-manifest',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
 c=json.loads(args.contract.read_text());
 if c['permissions']['standalone_projector_training_allowed'] is not True or c['permissions']['m4_fusion_training_allowed'] is not False or c['permissions']['beats_training_allowed'] is not False: raise PermissionError('训练合同不匹配')
 if sha256(args.feature_manifest)!=c['feature_manifest_sha256']: raise ValueError('feature manifest SHA漂移')
 rows=read_jsonl(args.feature_manifest); train=[]; val=[]; labels=sorted({e['event_label'] for r in rows for e in json.loads(Path(r['feature_path']).read_bytes()[:0] or b'{}').get('events',[])})
 # derive labels from payloads to preserve official event vocabulary
 all_items=[]
 for r in rows:
  d=torch.load(r['feature_path'],map_location='cpu',weights_only=False)
  items=event_windows(d); (train if r['official_split']=='train' else val).extend(items); all_items.extend(items)
 labels=sorted({label for _,label in all_items}); idx={x:i for i,x in enumerate(labels)}
 X=torch.stack([x for x,_ in train]); y=torch.tensor([idx[l] for _,l in train]); torch.manual_seed(17)
 model=nn.Sequential(nn.Linear(768,128),nn.LayerNorm(128),nn.GELU(),nn.Linear(128,len(labels)))
 opt=torch.optim.AdamW(model.parameters(),lr=float(c['model']['lr'])); history=[]
 steps=int(c['model']['steps']); rng=random.Random(17)
 for step in range(1,steps+1):
  sel=torch.tensor([rng.randrange(len(X)) for _ in range(min(256,len(X)))])
  logits=model(X[sel]); loss=F.cross_entropy(logits,y[sel]); opt.zero_grad(); loss.backward(); opt.step(); history.append({'step':step,'loss':float(loss.item())})
 with torch.no_grad():
  xv=torch.stack([x for x,_ in val]); yv=torch.tensor([idx[l] for _,l in val]); pred=model(xv).argmax(1); acc=float((pred==yv).float().mean())
 args.output.parent.mkdir(parents=True,exist_ok=True); torch.save({'state_dict':model.state_dict(),'labels':labels,'training_performed':True,'beats_training_performed':False},args.output.with_suffix('.pt'))
 args.output.write_text(json.dumps({'status':'PASS_STANDALONE_PROJECTOR_TRAINING','train_items':len(train),'validation_items':len(val),'classes':len(labels),'steps':steps,'final_loss':history[-1]['loss'],'validation_window_accuracy':acc,'beats_training_performed':False,'m4_fusion_training_performed':False,'test_accessed':False,'history':history},ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':'PASS_STANDALONE_PROJECTOR_TRAINING','train_items':len(train),'validation_items':len(val),'classes':len(labels),'final_loss':history[-1]['loss'],'validation_window_accuracy':acc},ensure_ascii=False))
if __name__=='__main__': main()
