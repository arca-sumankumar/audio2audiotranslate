# Models

What real models exist at each pipeline stage, which ones the product uses by
default, and how each stage is evaluated. For large-file sizes and the
download procedures see `list_of_lf.txt` (repo root).

## How models plug in

The pipeline is `STT → MT → TTS`. Each stage runs **one** model per job:

- By default every stage is a deterministic **mock** (fake transcripts, beep
  TTS) so the stack runs with zero downloads.
- `make local-real` (or `STTS_MODEL_BACKEND` per service) switches each stage
  to its real backend.
- The **MT model is also selectable per request** (demo dropdown, or the
  `model` WS/REST field) — that is the only stage a client can switch.

| Stage | Default (shipped) | Alternatives (evaluated) |
|---|---|---|
| STT | faster-whisper large-v3-turbo | IndicConformer-600M (auto, gu/ml/mr) · whisper large-v3 (baseline only) |
| MT | NLLB-200 distilled 600M | IndicTrans2 · Bergamot (not installed) |
| TTS | Piper | — |

So the default real configuration = **Whisper turbo + NLLB + Piper** (+
IndicConformer for gu/ml/mr). The other downloaded models are candidates, not
defaults.

## STT stage (speech → native script)

**Default: faster-whisper large-v3-turbo** — OpenAI Whisper converted to
CTranslate2 (int8, CPU). Language is forced from the caller
(`sourceLanguage`); never auto-detected. Silero VAD, beam 5, conservative
hallucination guard.

**Routing (shipped):** gu/ml/mr are handled by **IndicConformer-600M** (CTC)
instead of Whisper — Whisper hallucinates on real gu/ml/mr audio
([REAL_NATIVE_EVAL.md](REAL_NATIVE_EVAL.md) §4.2), IndicConformer doesn't
(§4.5). `make_asr()` in `common/stts_core/models.py` returns the router
(`RoutingASR`, `common/stts_core/backends.py`); verification in
[E2E_ROUTING_VERIFICATION.md](E2E_ROUTING_VERIFICATION.md). IndicConformer
renders inline English words phonetically into the native script and spells
numerals as words — a known MT-facing caveat (see MT stage).

**whisper large-v3** (full, non-turbo) is downloaded as an **evaluation
baseline only** — never used by the product.

**Medical context:** when a known `sourceLanguage` is supplied, the ASR applies
a per-language `initial_prompt` plus hotwords (symptoms, drugs, units) and a
`hallucination_silence_threshold` to preserve English medical terms spoken in
Indian-language audio. Overrides: `STTS_ASR_INITIAL_PROMPT`,
`STTS_ASR_HOTWORDS`, `STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD`
(`common/stts_core/medical.py`, LLD2 §12.6). For **Hindi the hotwords are
disabled by default** — `sot_prev` injection measurably hurts Hindi WER
(0.638 → 0.792, REAL_NATIVE_EVAL.md §4.3) — while the native-script prompt
stays on.

**How STT is evaluated:** hallucination rate + WER/CER vs gold transcripts on
real-native sets (eQOURSE gu/ml/mr/hi, ekacare clinical hi). Full numbers and
rerun procedure: [REAL_NATIVE_EVAL.md](REAL_NATIVE_EVAL.md).

## MT stage (native script → English)

**Default: NLLB-200 distilled 600M** (CTranslate2 int8, CPU) — the only model
guaranteed to cover all 11 languages incl. indic→indic. The MT model is
switchable per session (dropdown / `model` field); a model whose files or
packages aren't installed fails cleanly (`error` event / job `failed`), never
crashes the stack.

| Model (`id`) | Languages | flores200-plus BLEU (en→xx / xx→en) | Size | License | Runtime |
|---|---|---|---|---|---|
| **NLLB-200 distilled 600M** (`nllb`, baseline) | all 11 | — | ~1.1 GB | CC-BY-NC-4.0 | CTranslate2 int8 (CPU) |
| **AI4Bharat IndicTrans2** (`indictrans2`, 1.1B) | en↔all 11 | — | 2 × ~1 GB weights (≈8 GB disk, ships both `pytorch_model.bin` and `model.safetensors`) | MIT | transformers + torch (CPU-slow) |
| **Mozilla Firefox Translations** (`bergamot`, tiny) | en↔gu/hi/kn/ml/ta | gu 24.1 / 32.5 · hi 35.6 / 36.1 · kn 21.1 / 28.9 · ml 18.7 / 30.0 · ta 22.5 / 27.9 | ~17 MB/pair | MPL-2.0 | `fxtranslate` (native, CPU-fast) |

- `bergamot` pairs are English-centric (en→xx / xx→en only) and per-direction
  model files; `indictrans2` is likewise English↔Indic — use `nllb` for
  indic→indic pairs. **Bergamot is not installed** in this workspace (demo
  preset fails with `MT_ERROR`).
- IndicTrans2 uses `trust_remote_code=True` (the checkpoints ship custom
  modeling/tokenization code) and its en→indic checkpoints emit Devanagari as
  an intermediate script: the backend transliterates the output to the native
  script (gu/ta/kn/ml/bn/te/pa/or/as) before returning it. KV cache is disabled
  (`use_cache=False`) because the custom modeling code is incompatible with the
  modern `transformers` cache format; expect ~2-10 s/sentence on CPU, so it is
  practical for batch demos but slower than `nllb` for streaming.
- For a **Hindi → English** demo: NLLB (default, fast) or IndicTrans2 (best
  hi→en quality, slow). **Bergamot** hi↔en needs `make models` +
  `pip install fxtranslate`.

**Known MT bugs (measured, see [MT_EVAL.md](MT_EVAL.md)):** NLLB truncates
longer inputs and occasionally emits garbage; Malayalam number-words
(IndicConformer spells numerals as words) make both MT models re-derive wrong
numbers (`299` → `Rs. 2,999`) or loop.

**How MT is evaluated:** reference-free cascade-gap (EN(gold) vs EN(STT)) +
English-term/number fidelity + model agreement, over the real-native chain
with the shipped routing. Full tables and rerun procedure:
[MT_EVAL.md](MT_EVAL.md).

## TTS stage (English → speech)

**Default: Piper** (fast neural TTS). Voices ship for **English**
(`en_US-lessac-medium`), **Hindi** (`hi_IN-pratham-medium`) and **Malayalam**
(`ml_IN-arjun-medium`); every other target language falls back to the mock
beep (no Tamil/Telugu/etc. voices yet).

**Caveat:** Piper's synthetic Malayalam is out-of-distribution for Whisper, so
`ms-*` preset translations are unreliable — use real human recordings for
usable `ml → …` output (the Gujarati presets already expect recorded audio).

**How TTS is evaluated:** not benchmarked beyond voice availability; it is the
limiting step of the pipeline (few voices, synthetic audiodistribution gap).

## Evaluation workflow (batch + streaming)

The project's main goal is evaluating candidate **MT** models in both batch and
live streaming modes (the demo dropdown applies to both demos). Workflow: pick
a model in the dropdown, run the batch preset (source + translated transcript
boxes) and a streaming mic session, and compare. Streaming correctness
criteria: (1) no repeated words, (2) no missed words across the confirm/trim
boundaries; batch is scored against the recorded `data/test_audio/*.txt`
transcripts via `make test-batch` ([TESTING.md](TESTING.md)).

STT and MT have separate scored evaluations (REAL_NATIVE_EVAL.md §7 and
MT_EVAL.md §6 respectively); both use the eval harnesses in
[TESTING.md](TESTING.md).
