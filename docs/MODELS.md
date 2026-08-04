# Models

Real offline backends, the model catalogue, and how to evaluate MT models.
For the large-file download procedures see `list_of_lf.txt` (repo root).

## Real models (optional)

By default the pipeline runs with deterministic **mock** backends (fake
transcripts, beep TTS) so the full flow works with zero model downloads. To
use real offline models — faster-whisper large-v3-turbo (ASR, int8),
NLLB-200 distilled 600M (MT, int8), Piper (TTS), plus the Bergamot / IndicTrans2
MT candidates — download them once, then start the stack with real backends:

```bash
make models        # whisper, nllb-600m, piper, bergamot/ (per-pair), indictrans2/ (2 x ~1 GB)
make local-real    # start the stack with STTS_MODEL_REAL=1
```

- TTS voices ship for **English**, **Hindi** and **Malayalam**; other target
  languages fall back to the mock beep (Piper has no Tamil/Telugu/etc. voices
  yet).
- The source language is taken from the caller — the WS `sourceLanguage`
  query param, the batch `sourceLanguage` field, or the demo preset — and
  used to seed Whisper. It only falls back to auto-detection when absent,
  and auto-detection can mislabel Malayalam as Tamil, so pass the language
  explicitly when you know it.
- Piper's synthetic Malayalam (`ml_IN-arjun`) is out-of-distribution for
  Whisper, so `ms-*` preset translations are unreliable. Use real human
  recordings for usable `ml → …` output; the Gujarati presets already expect
  recorded audio.
- `make local-smoke` still uses the mock stack (`make local-up`); it uploads a
  sine tone, which a real Whisper correctly transcribes as silence.
- Model backends are also selectable per service via
  `STTS_MODEL_BACKEND={mock|whisper|nllb|piper|bergamot|indictrans2}` and
  `STTS_MODEL_OFFLINEPATH=<dir>`; the MT model per *session* comes from the
  demo dropdown (`model` WS/REST field), overriding the worker default.
- When a known `sourceLanguage` is supplied, the ASR applies a medical-domain
  context — a per-language `initial_prompt` plus hotwords (symptoms, drugs,
  units) and a conservative `hallucination_silence_threshold` — to preserve the
  English medical terms spoken inside Indian-language audio. Override any of
  them with `STTS_ASR_INITIAL_PROMPT`, `STTS_ASR_HOTWORDS`,
  `STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD` (see `common/stts_core/medical.py`
  and LLD2 §12.6). For **Hindi the hotwords are disabled by default** — the
  `sot_prev` injection measurably hurts Hindi WER on real audio (0.638 → 0.792,
  see [REAL_NATIVE_EVAL.md](REAL_NATIVE_EVAL.md) §4.3) — while the
  native-script prompt stays on.

## ASR routing (shipped)

The ASR worker routes by `sourceLanguage`: **gu/ml/mr → IndicConformer-600M
CTC**, everything else → faster-whisper large-v3-turbo (see
[E2E_ROUTING_VERIFICATION.md](E2E_ROUTING_VERIFICATION.md)). `make_asr()` in
`common/stts_core/models.py` returns the router; `IndicConformerASR` lives in
`common/stts_core/backends.py`.

## Model evaluation (batch + streaming)

Evaluating candidate **machine-translation** models in BOTH batch and live
streaming modes is the project's main goal. Every admitted model must cover
**Gujarati and Tamil** (plus the other Indic languages) and be open source.

The demo UI has a **Translation model** dropdown at the top that applies to
both demos (batch and streaming); the selection flows through as the WS
`model` query param or the batch `model` JSON field and switches the MT worker
per session.

| Model (`id`) | Languages | flores200-plus BLEU (en→xx / xx→en) | Size | License | Runtime |
|---|---|---|---|---|---|
| **NLLB-200 distilled 600M** (`nllb`, baseline) | all 11 | — | ~1.1 GB | CC-BY-NC-4.0 | CTranslate2 int8 (CPU) |
| **Mozilla Firefox Translations** (`bergamot`, tiny) | en↔gu/hi/kn/ml/ta | gu 24.1 / 32.5 · hi 35.6 / 36.1 · kn 21.1 / 28.9 · ml 18.7 / 30.0 · ta 22.5 / 27.9 | ~17 MB/pair | MPL-2.0 | `fxtranslate` (native, CPU-fast) |
| **AI4Bharat IndicTrans2** (`indictrans2`, 1.1B) | en↔all 11 | — | 2 × ~1 GB weights (≈8 GB disk, ships both `pytorch_model.bin` and `model.safetensors`) | MIT | transformers + torch (CPU-slow) |

- `bergamot` pairs are English-centric (en→xx / xx→en only) and per-direction
  model files; `indictrans2` is likewise English↔Indic — use `nllb` for
  indic→indic pairs.
- Download them with `make models` (Bergamot files are pulled from Mozilla's
  registry and hash-verified; IndicTrans2 pulls the two official ai4bharat
  checkpoints, which are **gated** on Hugging Face — accept the terms on
  `ai4bharat/indictrans2-en-indic-1B` and `-indic-en-1B` and run
  `huggingface-cli login` first). Then: `pip install fxtranslate` for
  `bergamot`, and `pip install torch transformers` for `indictrans2`.
- IndicTrans2 uses `trust_remote_code=True` (the checkpoints ship custom
  modeling/tokenization code) and its en→indic checkpoints emit Devanagari as
  an intermediate script: the backend transliterates the output to the native
  script (gu/ta/kn/ml/bn/te/pa/or/as) before returning it. KV cache is disabled
  (`use_cache=False`) because the custom modeling code is incompatible with the
  modern `transformers` cache format; expect ~2-10 s/sentence on CPU, so it is
  practical for batch demos but slower than `bergamot`/`nllb` for streaming.
- Selecting a model whose files/packages aren't installed does not crash the
  stack: the MT worker surfaces an `error` event, the batch job is marked
  `failed` with the reason, and the streaming client is told to stop.
- Evaluation workflow: pick a model in the dropdown, run the batch preset
  (source + translated transcript boxes) and a streaming mic session, and
  compare. Streaming correctness criteria are (1) no repeated words and (2) no
  missed words across the confirm/trim boundaries; batch is scored against the
  recorded `data/test_audio/*.txt` transcripts via `make test-batch` (see
  [TESTING.md](TESTING.md)).

For a **Hindi → English** demo, select **NLLB** (the configured default; fast)
or **IndicTrans2** (best hi→en quality per [MT_EVAL.md](MT_EVAL.md);
slow on CPU, ~2–10 s/sentence).
**Bergamot** hi↔en requires `make models` + `pip install fxtranslate` first —
without them the job fails with a clear `MT_ERROR`.

## Measured MT quality

Per-language translation quality of the product chain (reference-free) is in
[MT_EVAL.md](MT_EVAL.md). Recommendation: hi → IndicTrans2, gu → NLLB,
ml → normalize IndicConformer number-words to digits before MT, mr → mixed.
