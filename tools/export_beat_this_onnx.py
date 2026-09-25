"""Regenerate app/automix/analysis/models/beat_this_final0_int8.onnx (developer tool, not shipped).

    pip install torch beat-this onnx onnxscript onnxruntime   # a separate venv is fine
    python tools/export_beat_this_onnx.py

Exports Beat This!'s ``final0`` network with a variable-length time axis
(the runtime feeds 1500-frame chunks, shorter for a piece under 30 s, exactly
like beat_this.inference) and quantizes its weights to int8 (~21 MB instead
of ~79 MB; beats/downbeats F 0.997/0.995 against the float PyTorch model on
real music). Bump BeatThisOnnxAnalysisProvider.ONNX_VERSION when the output
changes, so cached analyses are redone.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import torch
from beat_this.inference import load_model
from onnxruntime.quantization import QuantType, quantize_dynamic

TARGET = Path(__file__).resolve().parents[1] / "app" / "automix" / "analysis" / "models" / "beat_this_final0_int8.onnx"


class _Logits(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, spect: torch.Tensor):
        output = self.model(spect)
        return output["beat"], output["downbeat"]


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        float_model = Path(directory) / "beat_this_final0.onnx"
        torch.onnx.export(
            _Logits(load_model("final0", "cpu")), (torch.zeros(1, 1500, 128),), str(float_model),
            input_names=["spect"], output_names=["beat", "downbeat"],
            dynamic_axes={"spect": {1: "frames"}, "beat": {1: "frames"}, "downbeat": {1: "frames"}},
            opset_version=17, dynamo=False,
        )
        quantize_dynamic(str(float_model), str(TARGET), weight_type=QuantType.QInt8)
    print(f"wrote {TARGET} ({TARGET.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
