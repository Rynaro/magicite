# Prospective factual correction C03 — expected model file count

Before any query execution, the authoritative expected-model manifest is `/private/tmp/magicite-v1-empirical/expected-model.json`, SHA256 `dd1d94b057aed4d5ca58fe1959f99492594dc4ca3157ccd863604247ece22b24`. Its `files` mapping contains exactly **13 paths**, not14. Earlier plan/scout prose saying14 was a counting error; no explanation for that historical error is established here.

The exact frozen mapping below controls verification. This note changes no expected model pin, file/path/digest, model bytes, source, run binding, acceptance criterion or threshold. It does not waive a missing file: all13 listed paths remain required under the existing verifier, with no omitted or substituted entry. The count describes manifest-listed paths, not a new claim about unique physical blob count or upstream authenticity.

Root reports VIGIL independently verified all13 bound entries during the C3 train audit and the current C3 run freeze records13. RAMZA's check for this note independently read the unchanged manifest, counted its mapping and recomputed its manifest SHA256; it did not rerun model computation or alter an active preparation.

```json
{
  "models--qdrant--bge-small-en-v1.5-onnx-q/blobs/0d7726d0cdccb62ee17c03bd1595cff07199b8f8": "13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd",
  "models--qdrant--bge-small-en-v1.5-onnx-q/blobs/51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431": "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431",
  "models--qdrant--bge-small-en-v1.5-onnx-q/blobs/688882a79f44442ddc1f60d70334a7ff5df0fb47": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
  "models--qdrant--bge-small-en-v1.5-onnx-q/blobs/75305659f7795d4549f0e23688b52fa20a32f925": "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
  "models--qdrant--bge-small-en-v1.5-onnx-q/blobs/9bbecc17cabbcbd3112c14d6982b51403b264bfa": "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
  "models--qdrant--bge-small-en-v1.5-onnx-q/files_metadata.json": "6189cb8e1bbfa56c4a6ee6b0fa3e92c9c774ec7d7ffeb3d8bb5456810888334e",
  "models--qdrant--bge-small-en-v1.5-onnx-q/refs/main": "5821be71e4de7a1e0abd47d130583a7c38ec6ed676fdf8d0d532a168edb60438",
  "models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/aa8f8b060edb00e03bfdd08813a2949946c8ba55/config.json": "13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd",
  "models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/aa8f8b060edb00e03bfdd08813a2949946c8ba55/model_optimized.onnx": "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431",
  "models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/aa8f8b060edb00e03bfdd08813a2949946c8ba55/special_tokens_map.json": "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
  "models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/aa8f8b060edb00e03bfdd08813a2949946c8ba55/tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
  "models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/aa8f8b060edb00e03bfdd08813a2949946c8ba55/tokenizer_config.json": "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
  "models--qdrant--bge-small-en-v1.5-onnx-q/trees/aa8f8b060edb00e03bfdd08813a2949946c8ba55.json": "4da434babbdfea1afb640c0792caf42f224e06c1cfc4fb152f7b3d27ed6f23c3"
}
```

The frozen plan/envelope and original historical wording remain preserved; this correction is attached prospectively. All ten criteria remain unchanged. The expected-byte identity remains self-observed, not publisher-authenticated model provenance.
