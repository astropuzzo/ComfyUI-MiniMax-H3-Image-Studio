from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

try:
    import torch
except ImportError:  # The release itself has no dependencies outside ComfyUI.
    torch = None


def load_nodes_with_stubs():
    comfy = types.ModuleType("comfy")
    comfy.__path__ = []
    model_management = types.ModuleType("comfy.model_management")
    model_sampling = types.ModuleType("comfy.model_sampling")
    samplers = types.ModuleType("comfy.samplers")
    nested_tensor = types.ModuleType("comfy.nested_tensor")
    utils = types.ModuleType("comfy.utils")
    node_helpers = types.ModuleType("node_helpers")

    class ModelSamplingDiscreteFlow:
        def __init__(self, model_config=None):
            self.multiplier = 1000
            self.noise_scale = 1.0

        def set_parameters(self, shift=1.0, timesteps=1000, multiplier=1000):
            self.shift = shift
            self.multiplier = multiplier

        def set_noise_scale(self, value):
            self.noise_scale = value

    class ConstMixin:
        pass

    model_sampling.ModelSamplingDiscreteFlow = ModelSamplingDiscreteFlow
    model_sampling.CONST = ConstMixin
    samplers.SCHEDULER_NAMES = ["simple"]
    samplers.SAMPLER_NAMES = ["res_multistep", "er_sde", "sa_solver"]

    comfy.model_management = model_management
    comfy.model_sampling = model_sampling
    comfy.samplers = samplers
    comfy.nested_tensor = nested_tensor
    comfy.utils = utils
    for name, module in {
        "comfy": comfy,
        "comfy.model_management": model_management,
        "comfy.model_sampling": model_sampling,
        "comfy.samplers": samplers,
        "comfy.nested_tensor": nested_tensor,
        "comfy.utils": utils,
        "node_helpers": node_helpers,
    }.items():
        sys.modules[name] = module

    path = Path(__file__).resolve().parents[1] / "nodes.py"
    spec = importlib.util.spec_from_file_location("minimax_h3_nodes_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(torch is not None, "PyTorch is supplied by ComfyUI, not this dependency-free repository")
class RuntimeNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = load_nodes_with_stubs()

    def test_decode_recommended_strategy_uses_connected_index(self):
        frames = torch.rand(5, 16, 16, 3)
        result = self.nodes.H3ImageFrameSelector().select(
            frames=frames,
            strategy="decode_recommended",
            manual_index=0,
            skip_first_frames=0,
            candidate_start=0.0,
            candidate_end=1.0,
            similarity_weight=0.6,
            top_k=4,
            recommended_index=3,
        )
        self.assertEqual(result[2], 3)
        self.assertEqual(tuple(result[0].shape), (1, 16, 16, 3))
        self.assertTrue(torch.equal(result[0][0], frames[3]))

    def test_decode_appends_recommended_index_without_reordering_old_outputs(self):
        class Vae:
            @staticmethod
            def decode(_latent):
                return torch.rand(5, 8, 8, 3)

        samples = {
            "samples": torch.zeros(1, 1, 1, 1),
            "h3_context_frames": 5,
            "h3_output_strategy": "fixed",
            "h3_output_frame_index": 2,
        }
        frames, decoded_frames, info, recommended_index = self.nodes.H3ImageDecode().decode(samples, Vae())
        self.assertEqual(tuple(frames.shape), (5, 8, 8, 3))
        self.assertEqual(decoded_frames, 5)
        self.assertIn("Preferred still", info)
        self.assertEqual(recommended_index, 2)

    def test_legacy_sampling_fallback_loads_without_model_sampling_av(self):
        class OriginalSampling:
            multiplier = 1000
            noise_scale = 0.5

        class FakeModel:
            def __init__(self):
                self.model = types.SimpleNamespace(model_config=None)
                self.model_options = {"transformer_options": {}}
                self.sampling = OriginalSampling()

            def clone(self):
                return FakeModel()

            def get_model_object(self, _name):
                return self.sampling

            def add_object_patch(self, _name, value):
                self.sampling = value

        shifted, backend = self.nodes.H3SamplingSettings._apply_h3_shift(FakeModel(), 12.0, 3.0)
        self.assertEqual(backend, "ModelSamplingAV compatibility shim")
        self.assertEqual(shifted.sampling.shift, 12.0)
        self.assertEqual(shifted.sampling.noise_scale, 0.5)
        self.assertEqual(shifted.sampling.audio_scale, 4.0)

    def test_lightx_profiles_are_adapter_specific(self):
        self.assertEqual(
            self.nodes.SAMPLING_PROFILES["LightX v0.1 | ER-SDE 4 steps"],
            ("er_sde", "simple", 4, 12.0, 3.0),
        )
        self.assertEqual(
            self.nodes.SAMPLING_PROFILES["LightX v0.1 | SA-Solver 4 steps"],
            ("sa_solver", "simple", 4, 12.0, 3.0),
        )


if __name__ == "__main__":
    unittest.main()
