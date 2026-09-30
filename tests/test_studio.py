"""Single-frame, editable conditioning and decoder regression tests, CPU only."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import torch

from test_nodes_runtime import load_nodes_with_stubs


def load_studio():
    with patch.dict(sys.modules):
        legacy = load_nodes_with_stubs()
        comfy = sys.modules["comfy"]
        comfy.model_management.load_models_gpu = lambda *_args, **_kwargs: None
        comfy.samplers.sampler_object = lambda name: name
        comfy.samplers.calculate_sigmas = lambda _model, _schedule, steps: torch.linspace(1, 0, steps+1)

        class Field:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.__dict__.update(kwargs)

        class ComfyNode:
            @classmethod
            def GET_SCHEMA(cls):
                return cls.define_schema()

        class NodeOutput(tuple):
            def __new__(cls, *values):
                return tuple.__new__(cls, values)

        io = types.SimpleNamespace(ComfyNode=ComfyNode, Schema=Field, NodeOutput=NodeOutput)
        for name in ("Combo", "Int", "Float", "String", "Image"):
            setattr(io, name, types.SimpleNamespace(Input=Field, Output=Field))
        io.Custom = lambda _type: types.SimpleNamespace(Input=Field, Output=Field)
        io.Autogrow = types.SimpleNamespace(Input=Field, TemplateNames=Field)
        latest = types.ModuleType("comfy_api.latest")
        latest.io = io
        sys.modules["comfy_api.latest"] = latest
        folders = types.ModuleType("folder_paths")
        folders.get_filename_list = lambda _category: []
        sys.modules["folder_paths"] = folders
        sys.modules["nodes"] = types.ModuleType("nodes")
        sampler_module = types.ModuleType("comfy_extras.nodes_custom_sampler")
        sampler_module.BasicGuider = types.SimpleNamespace(execute=lambda model, cond: ((model, cond),))
        sampler_module.Noise_RandomNoise = lambda seed: types.SimpleNamespace(seed=seed)
        sampler_module.SamplerCustomAdvanced = types.SimpleNamespace(execute=lambda noise, guider, sampler, sigmas, latent: (latent,))
        sys.modules[sampler_module.__name__] = sampler_module

        root = Path(__file__).resolve().parents[1]
        package = types.ModuleType("studio_test_package")
        package.__path__ = [str(root)]
        sys.modules[package.__name__] = package
        sys.modules[package.__name__ + ".nodes"] = legacy
        for name in ("still_decode", "studio"):
            spec = importlib.util.spec_from_file_location(package.__name__ + "." + name, root / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        return module, comfy


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.studio, cls.comfy = load_studio()

    def pipeline(self):
        class Clip:
            def tokenize(self, prompt, **kwargs):
                self.prompt, self.kwargs = prompt, kwargs
                return prompt

            def encode_from_tokens_scheduled(self, tokens):
                return [[torch.zeros(1, 1), {}]]

        class Vae:
            def __init__(self):
                self.encoded = []

            def encode(self, image):
                self.encoded.append(image)
                return torch.zeros(1, 24, 1, image.shape[1]//16, image.shape[2]//16)

        return {"clip": Clip(), "vae": Vae()}

    def test_generation_is_one_frame_and_has_no_reference_anchor(self):
        models = self.pipeline()
        job = self.studio.H3StudioGenerate.execute(models, "a red mug", "1:1 square", 3.0)[0]
        video, audio = job["latent"]["samples"].unbind()
        self.assertEqual(tuple(video.shape[:3]), (1, 24, 1))
        self.assertEqual(tuple(audio.shape), (1, 32, 2, 2))
        self.assertEqual(models["clip"].prompt, "a red mug")
        self.assertEqual(models["clip"].kwargs, {"images": []})
        self.assertEqual(job["positive"][0][1], {})

    def test_edit_references_are_ordered_and_never_lock_source_frame(self):
        models = self.pipeline()
        source = torch.full((2, 64, 96, 3), 0.1)
        second = torch.full((2, 64, 96, 3), 0.2)
        third = torch.full((1, 64, 96, 3), 0.3)
        instruction = "Person from <Picture 1>, pose from <Picture 2>, room from <Picture 3>."
        job = self.studio.H3StudioEdit.execute(
            models, source, instruction, "source image", 1,
            references={"reference_image_3": third, "reference_image_2": second})[0]
        self.assertEqual(models["clip"].prompt, instruction)
        self.assertEqual(job["references"], 3)
        self.assertEqual([round(float(image.mean()), 1) for image in models["vae"].encoded], [0.1, 0.2, 0.3])
        self.assertTrue(all(image.shape[0] == 1 for image in models["vae"].encoded))
        self.assertEqual(len(job["positive"][0][1]["minimax_refs"]), 3)
        self.assertNotIn("minimax_keyframes", job["positive"][0][1])
        self.assertEqual(job["latent"]["samples"].unbind()[0].shape[2], 1)

    def test_single_source_instruction_gets_only_picture_tag(self):
        models = self.pipeline()
        job = self.studio.H3StudioEdit.execute(models, torch.zeros(1, 64, 64, 3),
                                              "remove the background", "source image", 1)[0]
        self.assertEqual(job["prompt"], "<Picture 1>. remove the background")

    def test_nine_references_and_invalid_extra_socket(self):
        models = self.pipeline()
        source = torch.zeros(1, 64, 64, 3)
        extras = {name: source for name in self.studio.REFERENCE_NAMES}
        job = self.studio.H3StudioEdit.execute(models, source, "combine pictures", "source image", 1, references=extras)[0]
        self.assertEqual(job["references"], 9)
        with self.assertRaises(ValueError):
            self.studio.H3StudioEdit.execute(models, source, "edit", "source image", 1, references={"reference_image_10": source})

    def test_resolution_above_one_mp_and_custom_dimensions(self):
        w, h = self.studio.canvas("16:9 landscape", 8)
        self.assertEqual((w % 32, h % 32), (0, 0))
        self.assertGreater(w * h, 7 * 1024**2)
        self.assertEqual(self.studio.canvas("custom pixels", 1, width=2048, height=1536), (2048, 1536))
        with self.assertRaises(ValueError):
            self.studio.canvas("1:1 square", float("nan"))
        with self.assertRaises(ValueError):
            self.studio.canvas("custom pixels", 1, width=0)

    def test_decoder_denormalizes_repeats_and_keeps_only_frame_three(self):
        class Stage:
            latents_mean = torch.full((24,), 0.4)
            latents_std = torch.full((24,), 2.0)

            def _adaptive_decode(self, group):
                self.group = group
                return torch.arange(20).view(1, 1, 20, 1, 1).expand(1, 3, 20, 64, 64).float() / 20

            def _finalize_pixels(self, raw):
                return raw

        stage = Stage()
        vae = types.SimpleNamespace(first_stage_model=stage, vae_dtype=torch.float32,
                                    device="cpu", patcher=object(), memory_used_decode=lambda shape, dtype: 1)
        latent = torch.full((2, 24, 1, 4, 4), 0.2)
        images = self.studio.decode_still(vae, {"samples": latent})
        self.assertEqual(tuple(images.shape), (2, 64, 64, 3))
        self.assertTrue(torch.allclose(images, torch.full_like(images, 3/20)))
        self.assertEqual(stage.group.shape[2], 5)
        self.assertTrue(torch.allclose(stage.group, torch.full_like(stage.group, 0.8)))
        self.assertEqual(images.untyped_storage().nbytes(), images.numel() * images.element_size())
        with self.assertRaises(ValueError):
            self.studio.decode_still(vae, {"samples": latent.repeat(1, 1, 2, 1, 1)})
        with self.assertRaises(ValueError):
            self.studio.decode_still(types.SimpleNamespace(first_stage_model=object()), {"samples": latent})


if __name__ == "__main__":
    unittest.main()
