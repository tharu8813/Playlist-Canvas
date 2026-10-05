# YAMNet model and AudioSet labels

YAMNet: Copyright 2022 Google LLC, Apache License 2.0 (see YAMNET_LICENSE.txt).
Upstream: https://github.com/tensorflow/models/tree/master/research/audioset/yamnet
AudioSet ontology/class labels: Google, CC BY 4.0, https://research.google.com/audioset/
License: https://creativecommons.org/licenses/by/4.0/

ONNX conversion by Audio Magic, using tf2onnx, opset 15; weights were not changed.
Source: https://huggingface.co/audiomagic/yamnet-onnx
Pinned revision: f25b741c2f0bdc6d7e6db24b5fddda23347dbafd
The downloaded ONNX graph and CSV are unmodified. The LICENSE file was renamed
YAMNET_LICENSE.txt for bundling. This notice documents attribution and provenance.

SHA-256:

- yamnet.onnx: d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e
- yamnet_class_map.csv: cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2

Model inputs: 16 kHz mono float32 PCM. Music predictions are model scores,
not verified genres or calibrated probabilities; uncertain results stay unknown.
