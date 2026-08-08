import comfy.model_sampling

from .nodes import H3SamplingSettings, NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS


def _apply_h3_shift_av(model, shift_video: float, shift_audio: float):
    """Patch H3 sigma shifts without dropping ModelSamplingAV/audio_scale.

    MiniMax H3 samples a packed audio-video latent even in image-only workflows.
    ComfyUI therefore uses ModelSamplingAV; replacing it with a plain flow
    sampler removes audio_scale and crashes SamplerCustomAdvanced before decode.
    """
    m = model.clone()

    class ModelSamplingAdvanced(comfy.model_sampling.ModelSamplingAV, comfy.model_sampling.CONST):
        pass

    original = m.get_model_object("model_sampling")
    model_sampling = ModelSamplingAdvanced(m.model.model_config)
    multiplier = getattr(original, "multiplier", 1000)
    model_sampling.set_parameters(
        shift=float(shift_video),
        audio_shift=float(shift_audio),
        multiplier=multiplier,
    )
    if hasattr(original, "noise_scale"):
        model_sampling.set_noise_scale(original.noise_scale)
    m.add_object_patch("model_sampling", model_sampling)

    transformer_options = m.model_options.get("transformer_options", {}).copy()
    transformer_options["minimax_h3_sigma_shift_video"] = float(shift_video)
    transformer_options["minimax_h3_sigma_shift_audio"] = float(shift_audio)
    m.model_options["transformer_options"] = transformer_options
    return m


# Keep the existing node IDs and workflows intact while restoring ComfyUI's
# FLOW_AV contract required by MiniMax H3 sampling.
H3SamplingSettings._apply_h3_shift = staticmethod(_apply_h3_shift_av)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
