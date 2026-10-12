import importlib.util, sys
from pathlib import Path
import torch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('proto_manifest',ROOT/'scripts/build_unav_s1r_audio_prototype_manifest_v1.py')
mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)

def test_event_rows_use_overlap_weights(tmp_path):
 p={'video_id':'v','media_sha256':'m','timestamps':torch.tensor([[0.,1.],[1.,2.]]),'audio_embedding':torch.zeros(2,768),'events':[{'event_label':'x','onset':.5,'offset':1.5}]}
 out=mod.event_rows(p,tmp_path/'v.pt','train')[0]
 assert out['window_weights']==[.5,.5]
 assert out['event_label']=='x'
