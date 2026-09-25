"""Regenerate AutoMix's ONNX models in app/automix/analysis/models (developer tool, not shipped).

    pip install torch beat-this openunmix onnx onnxscript onnxruntime   # a separate venv is fine
    python tools/export_automix_onnx.py

- beat_this_final0_int8.onnx: Beat This!'s ``final0`` network with a
  variable-length time axis (the runtime feeds 1500-frame chunks, shorter for
  a piece under 30 s, exactly like beat_this.inference). Beats/downbeats
  F 0.997/0.995 against the float PyTorch model on real music.
- umxhq_vocals_int8.onnx: Open-Unmix ``umxhq``'s vocals network, stereo
  magnitude spectrogram in and out. Not ``umxl``: its weights are licensed
  for non-commercial use only. 99.5 % of 100 ms vocal-activity frames agree
  with the PyTorch model.

Both weights are quantized to int8. Bump BeatThisOnnxAnalysisProvider.ONNX_VERSION
when either output changes, so cached analyses are redone.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import torch
import torch.nn.functional as F
from onnxruntime.quantization import QuantType, quantize_dynamic

MODELS = Path(__file__).resolve().parents[1] / "app" / "automix" / "analysis" / "models"


class _BeatLogits(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, spect: torch.Tensor):
        output = self.model(spect)
        return output["beat"], output["downbeat"]


class _UmxVocals(torch.nn.Module):
    """openunmix.model.OpenUnmix.forward for one stereo item. The original reads
    the frame count through ``x.data.shape``, which export bakes into a constant."""

    def __init__(self, umx: torch.nn.Module) -> None:
        super().__init__()
        self.umx = umx

    def forward(self, magnitude: torch.Tensor) -> torch.Tensor:  # (1, 2, 2049, frames)
        m = self.umx
        x = magnitude.permute(3, 0, 1, 2)
        mix = x
        x = (x[..., :m.nb_bins] + m.input_mean) * m.input_scale
        x = torch.tanh(m.bn1(m.fc1(x.reshape(-1, 2 * m.nb_bins))).reshape(-1, 1, m.hidden_size))
        x = torch.cat([x, m.lstm(x)[0]], -1)
        x = m.bn3(m.fc3(F.relu(m.bn2(m.fc2(x.reshape(-1, x.shape[-1]))))))
        x = x.reshape(-1, 1, 2, m.nb_output_bins) * m.output_scale + m.output_mean
        return (F.relu(x) * mix).permute(1, 2, 3, 0)


def _export(module: torch.nn.Module, example: torch.Tensor, axes: dict[str, dict[int, str]], target: Path) -> None:
    """``axes``: every input/output name -> its variable-length (time) axis; the first is the input."""
    names = list(axes)
    with tempfile.TemporaryDirectory() as directory:
        float_model = Path(directory) / target.name
        torch.onnx.export(module.eval(), (example,), str(float_model), input_names=names[:1],
                          output_names=names[1:], dynamic_axes=axes, opset_version=17, dynamo=False)
        quantize_dynamic(str(float_model), str(target), weight_type=QuantType.QInt8)
    print(f"wrote {target} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    import openunmix
    from beat_this.inference import load_model

    frames = {1: "frames"}
    _export(_BeatLogits(load_model("final0", "cpu")), torch.zeros(1, 1500, 128),
            {"spect": frames, "beat": frames, "downbeat": frames}, MODELS / "beat_this_final0_int8.onnx")

    umx = openunmix.umxhq(targets=["vocals"], niter=0, device="cpu").target_models["vocals"].eval()
    vocals = _UmxVocals(umx).eval()
    probe = torch.rand(1, 2, 2049, 333)
    with torch.no_grad():
        assert torch.allclose(vocals(probe), umx(probe), atol=1e-5), "wrapper diverged from OpenUnmix.forward"
    _export(vocals, torch.rand(1, 2, 2049, 200), {"magnitude": {3: "frames"}, "vocals": {3: "frames"}},
            MODELS / "umxhq_vocals_int8.onnx")


if __name__ == "__main__":
    main()
