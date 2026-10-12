from __future__ import annotations
import json
import torch
from intersuit.train.audio_semantic_m4_adapter import (
    adapter_frozen_runtime_sha256,
    adapter_only_forward,
    adapter_pilot_loss,
    adapter_state_sha256,
    assert_finite_adapter_parameters,
    assert_finite_adapter_gradients,
    build_adapter_only_optimizer,
    build_m4_teacher_forced_example,
    build_teacher_forced_batch,
    build_teacher_forced_labels,
    counterfactual_ranking_coefficients,
    counterfactual_objective_coefficients,
    compare_counterfactual_nll,
    evaluate_counterfactual_gate,
    group_pilot_manifest,
    load_adapter_only_checkpoint,
    load_adapter_only_model,
    load_pilot_manifest,
    materialize_waveform,
    m4_teacher_forced_forward,
    prepare_real_condition_batch,
    save_adapter_only_checkpoint,
    summarize_counterfactual_nll,
)

def test_loader_and_silence(tmp_path):
    rows=[]
    for c in ('video_only','correct','mismatch','silence'): rows.append({'condition':c,'audio_source_path':None if c=='video_only' else 'x.wav'})
    p=tmp_path/'m.jsonl'; p.write_text('\n'.join(json.dumps(x) for x in rows)+'\n')
    assert len(load_pilot_manifest(p))==4
    assert materialize_waveform(rows[0],lambda _:torch.ones(8)) is None
    assert torch.equal(materialize_waveform(rows[3],lambda _:torch.ones(8)),torch.zeros(8))

def test_group_manifest_requires_shared_answer_and_four_conditions():
    rows=[{'pair_group_id':'g','condition':c,'question':'q','answer':'a'} for c in ('video_only','correct','mismatch','silence')]
    groups=group_pilot_manifest(rows)
    assert len(groups)==1 and list(groups[0]['conditions'])==['video_only','correct','mismatch','silence']
    rows[-1] = {**rows[-1], 'answer':'wrong'}
    try:
        group_pilot_manifest(rows)
    except ValueError as exc:
        assert '问题/答案' in str(exc)
    else:
        raise AssertionError('inconsistent counterfactual labels must fail closed')

def test_adapter_loss_updates_only_adapter():
    adapter=torch.nn.Linear(4,6); frozen=torch.nn.Linear(6,7); frozen.requires_grad_(False)
    x=torch.randn(1,2,4); correct=frozen(adapter(x)); mismatch=frozen(adapter(x+0.2)); target=torch.tensor([[1,2]])
    loss,metrics=adapter_pilot_loss(correct,mismatch,target,adapter(x)-adapter(x).detach())
    loss.backward()
    assert torch.isfinite(loss) and all(torch.isfinite(v) for v in metrics.values())
    assert adapter.weight.grad is not None and frozen.weight.grad is None


def test_counterfactual_ranking_coefficients_match_hinge_subgradient():
    active = counterfactual_ranking_coefficients(6.2, 6.1, margin=0.2)
    assert active['active'] is True and abs(float(active['hinge']) - 0.3) < 1e-12
    assert active['correct_coefficient'] == 2.0 and active['mismatch_coefficient'] == -1.0
    inactive = counterfactual_ranking_coefficients(5.0, 5.5, margin=0.2)
    assert inactive['active'] is False and inactive['correct_coefficient'] == 1.0


def test_counterfactual_objective_coefficients_add_benefit_and_silence_anchor():
    coefficients = counterfactual_objective_coefficients(
        {'video_only':2.0,'correct':2.2,'mismatch':2.1,'silence':2.3},
        mismatch_margin=0.2,video_benefit_margin=0.05,
        video_benefit_weight=1.0,silence_anchor_weight=0.2,
    )
    assert coefficients['ranking_active'] and coefficients['video_benefit_active']
    assert coefficients['correct_coefficient']==3.0
    assert coefficients['mismatch_coefficient']==-1.0
    assert coefficients['silence_coefficient']==0.2
    reverse = counterfactual_objective_coefficients(
        {'video_only':2.0,'correct':1.5,'mismatch':2.0,'silence':1.8},
        mismatch_margin=0.2,video_benefit_margin=0.05,
        video_benefit_weight=1.0,silence_anchor_weight=0.2,
    )
    assert reverse['correct_coefficient']==1.0 and reverse['silence_coefficient']==-0.2


def test_counterfactual_nll_summary_comparison_and_gate():
    pre_rows = [
        {'pair_group_id':'g0','condition_nll':{'video_only':2.0,'correct':2.2,'mismatch':2.1,'silence':2.1}},
        {'pair_group_id':'g1','condition_nll':{'video_only':3.0,'correct':3.1,'mismatch':3.2,'silence':3.2}},
    ]
    post_rows = [
        {'pair_group_id':'g0','condition_nll':{'video_only':2.0,'correct':1.8,'mismatch':2.2,'silence':2.05}},
        {'pair_group_id':'g1','condition_nll':{'video_only':3.0,'correct':2.8,'mismatch':3.3,'silence':3.05}},
    ]
    pre=summarize_counterfactual_nll(pre_rows); post=summarize_counterfactual_nll(post_rows)
    assert pre['correct_better_than_mismatch_accuracy']==0.5
    assert post['correct_better_than_mismatch_accuracy']==1.0
    comparison=compare_counterfactual_nll(pre,post)
    assert comparison['ordering_accuracy_delta']==0.5 and comparison['correct_mean_nll_reduction']>0
    thresholds={
        'post_ordering_accuracy_min':1.0,'ordering_accuracy_delta_min':0.5,
        'post_mismatch_margin_mean_min':0.4,'mismatch_margin_mean_delta_min':0.4,
        'correct_vs_video_accuracy_delta_min':1.0,'video_benefit_mean_delta_min':0.3,
        'correct_mean_nll_reduction_min':0.3,'post_silence_abs_gap_max':0.1,
        'silence_abs_gap_delta_max':0.0,
    }
    gate=evaluate_counterfactual_gate(pre,post,thresholds)
    assert gate['status']=='PASS_ADAPTER_CANARY20_METRICS' and all(gate['checks'].values())


def test_teacher_forced_batch_masks_prompt_and_real_forward_checks_loss():
    prompt=torch.tensor([[9,8]])
    answer=torch.tensor([[1,2,3]])
    input_ids, labels=build_teacher_forced_batch(prompt, answer)
    assert torch.equal(input_ids, torch.tensor([[9,8,1,2,3]]))
    assert torch.equal(labels, torch.tensor([[-100,-100,1,2,3]]))
    class Out:
        loss=torch.tensor(0.5)
    class Model:
        def __call__(self, **kwargs):
            assert kwargs['labels'].shape == kwargs['input_ids'].shape
            return Out()
    assert float(m4_teacher_forced_forward(Model(), input_ids=input_ids, labels=labels).loss) == 0.5

def test_loader_injection_configures_adapter_without_loading_checkpoint():
    class Enc(torch.nn.Module):
        def __init__(self):
            super().__init__(); self.m4_adapter=torch.nn.Linear(3,4)
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__(); self.scene_audio_encoder=Enc(); self.other=torch.nn.Linear(2,2)
        def get_scene_audio_encoder(self): return self.scene_audio_encoder
    def fake_loader(contract, device):
        return object(), Model(), object(), 1
    contract={'base_model':{'checkpoint':'x','m4_hidden_size':4},'audio_source':{'checkpoint':'p','sha256':'s'}}
    _, model, _, _, names=load_adapter_only_model(contract, loader=fake_loader)
    assert names == ('scene_audio_encoder.m4_adapter.bias','scene_audio_encoder.m4_adapter.weight')
    assert sum(p.requires_grad for p in model.parameters()) == 2

def test_teacher_forced_labels_and_forward_do_not_leak_mismatch_answer():
    class Tok:
        def __call__(self, text, **kwargs): return type('X', (), {'input_ids':torch.tensor([[1,2,3]])})()
    labels=build_teacher_forced_labels(Tok(),'Clock')
    adapter=torch.nn.Linear(768,4)
    frozen=torch.nn.Linear(4,5); frozen.requires_grad_(False)
    class Model(torch.nn.Module):
        def forward(self,x,video_context=None): return frozen(x)
    logits=adapter_only_forward(Model(),adapter,torch.randn(1,3,768),labels)
    assert logits.shape==(1,3,5)
    assert torch.equal(labels,torch.tensor([[1,2,3]]))


class _TinyAdapterModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.scene_audio_encoder = torch.nn.Module()
        self.scene_audio_encoder.semantic_projector = torch.nn.Linear(3, 3)
        self.scene_audio_encoder.m4_adapter = torch.nn.Linear(3, 4)
        self.streaming_av_module = torch.nn.Linear(4, 4)
        self.other = torch.nn.Linear(4, 5)

    def get_model(self):
        return self

    def get_scene_audio_encoder(self):
        return self.scene_audio_encoder


def test_optimizer_frozen_sha_gradient_and_checkpoint_contract(tmp_path):
    from intersuit.train.audio_semantic_m4_adapter import configure_adapter_only_model

    model = _TinyAdapterModel()
    names = configure_adapter_only_model(model)
    optimizer, optimizer_names = build_adapter_only_optimizer(model, learning_rate=1e-3)
    assert names == optimizer_names
    frozen_before = adapter_frozen_runtime_sha256(model)
    loss = model.other(model.scene_audio_encoder.m4_adapter(torch.randn(2, 3))).square().mean()
    loss.backward()
    norms = assert_finite_adapter_gradients(model, names)
    assert set(norms) == set(names)
    optimizer.step()
    assert set(assert_finite_adapter_parameters(model, names)) == set(names)
    assert adapter_frozen_runtime_sha256(model) == frozen_before
    adapter_sha = adapter_state_sha256(model)
    checkpoint = tmp_path / 'adapter.pt'
    digest = save_adapter_only_checkpoint(checkpoint, model, optimizer, step=1, metadata={'x': 1})
    assert len(digest) == 64
    with torch.no_grad():
        model.scene_audio_encoder.m4_adapter.weight.zero_()
    payload = load_adapter_only_checkpoint(checkpoint, model, optimizer)
    assert payload['step'] == 1 and adapter_state_sha256(model) == adapter_sha


def test_real_condition_batch_uses_shared_answer_and_condition_audio():
    class Tokenizer:
        bos_token_id = 1
        eos_token_id = 2
        def __call__(self, text, **kwargs):
            ids = [self.bos_token_id] + [3 + (ord(char) % 20) for char in str(text)]
            value = torch.tensor([ids]) if kwargs.get('return_tensors') == 'pt' else ids
            return type('Encoded', (), {'input_ids': value})()
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__(); self.weight=torch.nn.Parameter(torch.ones(1, dtype=torch.float32))
    tokenizer=Tokenizer(); model=Model()
    input_ids,labels=build_m4_teacher_forced_example(tokenizer,'What?','Clock')
    assert input_ids.shape == labels.shape and (labels == -100).any() and int(input_ids[0,-1]) == 2
    row={'condition':'silence','question':'What?','answer':'Clock','video_path':'v.mp4','audio_source_path':'a.wav'}
    def video_loader(*_args): return torch.ones(2,3,4,4)
    def audio_loader(qa,debug_condition):
        assert qa['scene_audio_path']=='a.wav' and debug_condition=='silence'
        return torch.zeros(3,8),torch.tensor([[0.,1.],[.5,1.5],[1.,2.]]),{}
    batch=prepare_real_condition_batch(row,tokenizer,model,object(),device='cpu',video_loader=video_loader,audio_loader=audio_loader)
    assert batch['scene_audios'].shape==(1,3,8) and batch['modalities']==['video']


def test_v4_semantic_projector_respects_runtime_bfloat16_dtype():
    from intersuit.model.scene_audio_encoder.scene_audio_encoder import FrozenBEATsV4SceneAudioEncoder

    encoder = FrozenBEATsV4SceneAudioEncoder.__new__(FrozenBEATsV4SceneAudioEncoder)
    torch.nn.Module.__init__(encoder)
    encoder.semantic_projector = torch.nn.Sequential(
        torch.nn.Linear(4, 2), torch.nn.GELU(), torch.nn.Linear(2, 4)
    ).to(dtype=torch.bfloat16)
    encoder.m4_adapter = torch.nn.Linear(4, 6).to(dtype=torch.bfloat16)
    output = encoder.project(torch.randn(2, 3, 4, dtype=torch.float32))
    assert output.shape == (2, 3, 6)
    assert output.dtype == torch.float32 and torch.isfinite(output).all()
    encoder.m4_adapter.float()
    encoder.m4_output_dtype = torch.bfloat16
    fp32_adapter_output = encoder.project(torch.randn(2, 3, 4, dtype=torch.bfloat16))
    assert encoder.m4_adapter.weight.dtype == torch.float32
    assert fp32_adapter_output.dtype == torch.bfloat16 and torch.isfinite(fp32_adapter_output.float()).all()
