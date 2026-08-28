import importlib.util
import sys
from pathlib import Path
import torch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v4_m4_adapter", ROOT / "scripts/adapt_unav_v4_to_m4_audio_projector_v1.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

def test_adapter_shape_and_gate0():
    model = MODULE.M4V4AudioAdapter()
    x = torch.randn(1, 3, 768)
    y = model(x)
    z = model(x, gate=0.0)
    assert y.shape == (1, 3, 3584)
    assert torch.equal(z, torch.zeros_like(z))

def test_v4_semantic_part_can_be_frozen():
    model = MODULE.M4V4AudioAdapter()
    for parameter in model.semantic.parameters():
        parameter.requires_grad = False
    assert all(not parameter.requires_grad for parameter in model.semantic.parameters())
    assert any(parameter.requires_grad for parameter in model.to_m4.parameters())
