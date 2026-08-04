# Testing

The test corpus, the make targets, and the manual/smoke checks. All commands
run from the repo root. The corpus lives in `data/test_audio/` (git-ignored;
reproducible, see §4).

## Doctor–patient test corpus

`data/test_audio/` is a mixed-language corpus used by the demo presets and the
batch test script. Each transcript mixes natural speech with inline English
medical terms (fever, temperature, blood test, prescription, headache, ...).

- `01_fever` … `05_backpain` — English (Piper `en_US-lessac`).
- `06_ml_fever` … `10_ml_jointpain` — Malayalam (Piper `ml_IN-arjun`).
- `11_gu_fever` … `15_gu_jointpain` — Gujarati transcripts only (no Piper gu
  voice); record `.wav` files as `NN_gu_<symptom>.wav` to enable them.
- `16_hi_followup` … `18_hi_fever` — **real clinical Hindi** recordings from
  the ekacare/eka-medical-asr-evaluation-dataset (auto-sets source `hi`), for
  the Hindi → English demo path.
- `19_gu_tvremote`, `20_ml_sadhya`, `21_mr_educationloan` — real-native
  routing demo presets (gu/ml/mr → IndicConformer), see
  [E2E_ROUTING_VERIFICATION.md](E2E_ROUTING_VERIFICATION.md).

```bash
make models       # once, needed for synthesis
make test-audio   # synthesize the missing WAVs from the .txt transcripts
make test-batch   # run every .wav through the real stack, print per-file results
```

`make test-batch` requires the **real** stack (`make local-real`) and submits
each WAV via the batch REST API, printing the source transcript next to the
translation plus a PASS/FAIL sanity check (English is reliable; Malayalam
passes the script-level check but its content remains unreliable per
[MODELS.md](MODELS.md)). Pass a target language to the script to translate
elsewhere: `.venv/bin/python scripts/test_batch_audio.py ta`.

To measure what the medical context does to the raw ASR output, run
`.venv/bin/python scripts/eval_asr.py [en|ml]` (decodes each WAV with and
without the context and reports glossary-term/number retention and WER deltas).

## Smoke / regression tests

```bash
make smoke          # E2E batch + streaming over docker compose
make local-smoke    # same suite against the localhost stack (mock backends)
make k3s-smoke      # same suite as a k3s Job
```

Unit tests are planned; the smoke test covers the full
ingest→asr→mt→tts→gateway/forwarder path.

## Making a test file for the batch API

The batch endpoint needs a WAV already on the shared volume. Generate a 5 s
tone on the host and copy it in:

```bash
docker run --rm -v "$(pwd)/data:/data" stts/tools:local \
  python -c "from stts_core.audio import synth_tone_wav; open('/data/sample.wav','wb').write(synth_tone_wav(5000))"
```

## Eval harnesses

| Script | What it measures |
|---|---|
| `scripts/eval_asr.py` | glossary-term/number retention + WER deltas with/without medical context on `data/test_audio/` |
| `scripts/measure_hallucination.py` | hallucination rate (empty / 4-gram repetition / Dice) per dir & model |
| `scripts/eval_indic_conformer.py`, `scripts/ic_full_run.py` | IndicConformer CTC/RNNT eval vs real-native golds |
| `scripts/eval_translation.py` | reference-free MT eval (cascade-gap + term/number fidelity) |

Run procedures for the real-native harnesses are in
[REAL_NATIVE_EVAL.md](REAL_NATIVE_EVAL.md) §7 and [MT_EVAL.md](MT_EVAL.md) §6.
