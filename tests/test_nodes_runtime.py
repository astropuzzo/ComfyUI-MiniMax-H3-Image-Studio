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

    class FakeNestedTensor:
        def __init__(self, tensors):
            self.tensors = tensors
            self.is_nested = True

        def unbind(self):
            return self.tensors

    model_sampling.ModelSamplingDiscreteFlow = ModelSamplingDiscreteFlow
    model_sampling.CONST = ConstMixin
    model_management.intermediate_device = lambda: torch.device("cpu")
    nested_tensor.NestedTensor = FakeNestedTensor
    utils.common_upscale = lambda samples, width, height, *_args: torch.nn.functional.interpolate(
        samples, size=(height, width), mode="bilinear", align_corners=False
    )
    node_helpers.conditioning_set_values = lambda conditioning, values: [
        [entry[0], {**entry[1], **values}] for entry in conditioning
    ]
    samplers.SCHEDULER_NAMES = ["simple"]
    samplers.SAMPLER_NAMES = ["res_multistep", "euler", "er_sde", "sa_solver"]

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

    def test_single_image_profile_builds_one_frame_av_latent(self):
        latent, requested, natural = self.nodes._empty_h3_av_latent(1344, 768, 1)
        video, audio = latent["samples"].unbind()
        self.assertEqual(requested, 1)
        self.assertEqual(natural, 1)
        self.assertEqual(tuple(video.shape), (1, 24, 1, 48, 84))
        self.assertEqual(tuple(audio.shape), (1, 32, 2, 2))

    def test_single_frame_i2i_uses_reference_conditioning(self):
        class Clip:
            def tokenize(self, prompt, **kwargs):
                self.prompt = prompt
                self.kwargs = kwargs
                return prompt

            @staticmethod
            def encode_from_tokens_scheduled(_tokens):
                return [[torch.zeros(1), {}]]

        class Vae:
            @staticmethod
            def encode(image):
                return torch.zeros((image.shape[0], 24, 1, 1, 1))

        clip = Clip()
        cond, latent, _source, frames, prompt, info = self.nodes.H3ImagePrepare().prepare(
            clip=clip,
            vae=Vae(),
            mode="image_to_image (FL2VA)",
            prompt="change the jacket to green",
            width=64,
            height=64,
            frame_preset=self.nodes.SINGLE_IMAGE_FRAME_PROFILE,
            optimize_prompt=True,
            preserve_strength=0.75,
            source_fit="crop_center",
            reference_size="max_identity_2048",
            source_image=torch.rand(1, 64, 64, 3),
        )
        self.assertEqual(frames, 1)
        self.assertIn("minimax_refs", cond[0][1])
        self.assertNotIn("minimax_keyframes", cond[0][1])
        self.assertIn("minimax_ref_items", clip.kwargs)
        self.assertIn("<Picture 1> is the source reference", prompt)
        self.assertIn("one-frame I2I uses Picture 1 reference conditioning", info)
        self.assertEqual(latent["h3_context_frames"], 1)

    def test_reference_sockets_use_one_picture_without_shifting_tags(self):
        primary_batch = torch.stack([
            torch.zeros(8, 8, 3),
            torch.ones(8, 8, 3),
        ])
        second = torch.full((1, 8, 8, 3), 0.5)
        references = self.nodes._collect_reference_images(primary_batch, (second, None))
        self.assertEqual(len(references), 2)
        self.assertTrue(torch.equal(references[0], primary_batch[:1]))
        self.assertTrue(torch.equal(references[1], second))

    def test_reference_prompt_assigns_explicit_picture_roles(self):
        prompt = self.nodes._normalize_prompt(
            "reference_edit (REF2VA)",
            "Keep the face from <Picture 1> and jacket from <Picture 2>.",
            True,
            0.75,
            2,
        )
        self.assertIn("target instructions take priority", prompt.lower())
        self.assertIn("<Picture 1> through <Picture 2>", prompt)
        self.assertIn("even when it conflicts with <Picture 1>", prompt)
        self.assertNotIn("preserve identity, pose, composition", prompt.lower())

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

    def test_decode_recommends_completed_edit_in_short_packet(self):
        frames = torch.stack([
            torch.zeros(16, 16, 3),
            torch.full((16, 16, 3), 0.20),
            torch.full((16, 16, 3), 0.80),
            torch.full((16, 16, 3), 0.82),
            torch.full((16, 16, 3), 0.81),
        ])

        class Vae:
            @staticmethod
            def decode(_latent):
                return frames

        samples = {
            "samples": torch.zeros(1, 1, 1, 1),
            "h3_context_frames": 5,
            "h3_output_strategy": "first_stable_edit",
        }
        _, _, info, recommended_index = self.nodes.H3ImageDecode().decode(samples, Vae())
        self.assertGreater(recommended_index, 0)
        self.assertIn("first_stable_edit", info)

    def test_decode_stable_quality_prefers_detailed_frame(self):
        frames = torch.full((5, 16, 16, 3), 0.5)
        checkerboard = (torch.arange(16).view(-1, 1) + torch.arange(16).view(1, -1)) % 2
        frames[2] = checkerboard.unsqueeze(-1).expand(-1, -1, 3).float() * 0.8 + 0.1

        class Vae:
            @staticmethod
            def decode(_latent):
                return frames

        samples = {
            "samples": torch.zeros(1, 1, 1, 1),
            "h3_context_frames": 5,
            "h3_output_strategy": "stable_quality",
        }
        _, _, info, recommended_index = self.nodes.H3ImageDecode().decode(samples, Vae())
        self.assertEqual(recommended_index, 2)
        self.assertIn("stable_quality", info)

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

    def test_detail_tone_lock_restores_broad_exposure_and_keeps_texture(self):
        source = torch.full((1, 64, 64, 3), 0.25)
        checker = ((torch.arange(64).view(-1, 1) + torch.arange(64).view(1, -1)) % 2).float()
        refined = torch.full((1, 64, 64, 3), 0.55) + checker.view(1, 64, 64, 1) * 0.08

        output, = self.nodes.H3DetailToneLock.lock_tone(source, refined, 1.0, 1.0, 8)

        self.assertLess(abs(float(output.mean()) - 0.25), 0.01)
        self.assertGreater(float(output.std()), 0.03)

    def test_detail_tone_lock_resizes_and_broadcasts_source(self):
        source = torch.rand(1, 32, 48, 3)
        refined = torch.rand(2, 64, 96, 3)

        output, = self.nodes.H3DetailToneLock.lock_tone(source, refined, 0.85, 0.45, 8)

        self.assertEqual(tuple(output.shape), (2, 64, 96, 3))

    def test_official_turbo_profiles_are_adapter_specific(self):
        self.assertEqual(
            self.nodes.SAMPLING_PROFILES["Turbo v1.0 | 8 steps"],
            ("euler", "simple", 8, 12.0, 3.0),
        )
        self.assertEqual(
            self.nodes.SAMPLING_PROFILES["Turbo v1.0 768p | 4 steps"],
            ("euler", "simple", 4, 6.0, 3.0),
        )
        self.assertEqual(
            self.nodes.SAMPLING_PROFILES["REF2VA Turbo v0.1 | 4 steps"],
            ("euler", "simple", 4, 12.0, 3.0),
        )
        self.assertEqual(
            self.nodes.SAMPLING_PROFILES["hybrid single image | ER-SDE 8 steps"],
            ("er_sde", "sgm_uniform", 8, 12.0, 3.0),
        )

    def test_old_lightx_profiles_still_load(self):
        self.assertEqual(
            self.nodes.LEGACY_SAMPLING_PROFILES["LightX v0.1 | ER-SDE 4 steps"],
            ("er_sde", "simple", 4, 12.0, 3.0),
        )

    def test_sampling_preset_exposes_custom_controls(self):
        inputs = self.nodes.H3ImageSamplingPreset.INPUT_TYPES()
        self.assertIn(self.nodes.CUSTOM_SAMPLING_PROFILE, inputs["required"]["sampling_profile"][0])
        self.assertEqual(inputs["optional"]["custom_steps"][1]["default"], 20)
        self.assertIn("beta_custom", inputs["optional"]["custom_scheduler"][0])

    def test_sampling_preset_custom_mode_routes_all_settings(self):
        captured = {}
        original_build = self.nodes.H3SamplingSettings.build

        def fake_build(_self, **kwargs):
            captured.update(kwargs)
            return "model", "sampler", "sigmas", "settings"

        self.nodes.H3SamplingSettings.build = fake_build
        try:
            result = self.nodes.H3ImageSamplingPreset().build(
                model="input-model",
                sampling_profile=self.nodes.CUSTOM_SAMPLING_PROFILE,
                custom_sampler="euler",
                custom_scheduler="beta_custom",
                custom_steps=7,
                custom_denoise=0.65,
                custom_shift_video=9.5,
                custom_shift_audio=2.5,
                custom_beta_alpha=0.7,
                custom_beta_beta=0.8,
            )
        finally:
            self.nodes.H3SamplingSettings.build = original_build

        self.assertEqual(captured["model"], "input-model")
        self.assertEqual(captured["sampler_name"], "euler")
        self.assertEqual(captured["scheduler"], "beta_custom")
        self.assertEqual(captured["steps"], 7)
        self.assertEqual(captured["denoise"], 0.65)
        self.assertEqual(captured["shift_video"], 9.5)
        self.assertEqual(captured["shift_audio"], 2.5)
        self.assertEqual(captured["beta_alpha"], 0.7)
        self.assertEqual(captured["beta_beta"], 0.8)
        self.assertIn(f"profile={self.nodes.CUSTOM_SAMPLING_PROFILE}", result[3])

    def test_fixed_sampling_preset_ignores_custom_values(self):
        captured = {}
        original_build = self.nodes.H3SamplingSettings.build

        def fake_build(_self, **kwargs):
            captured.update(kwargs)
            return "model", "sampler", "sigmas", "settings"

        self.nodes.H3SamplingSettings.build = fake_build
        try:
            self.nodes.H3ImageSamplingPreset().build(
                model="input-model",
                sampling_profile="base quality | RES 20 steps",
                custom_sampler="euler",
                custom_scheduler="beta_custom",
                custom_steps=2,
                custom_denoise=0.1,
                custom_shift_video=1.0,
                custom_shift_audio=1.0,
                custom_beta_alpha=2.0,
                custom_beta_beta=2.0,
            )
        finally:
            self.nodes.H3SamplingSettings.build = original_build

        self.assertEqual(captured["sampler_name"], "res_multistep")
        self.assertEqual(captured["scheduler"], "simple")
        self.assertEqual(captured["steps"], 20)
        self.assertEqual(captured["denoise"], 1.0)
        self.assertEqual(captured["shift_video"], 12.0)
        self.assertEqual(captured["shift_audio"], 3.0)


if __name__ == "__main__":
    unittest.main()
