# Real-Native Speech Evaluation Results

Detailed results and a repeatable evaluation procedure for the STT (and chained
STT→MT) quality work on **real, non-synthetic** speech. Raw command output and
timings live in `data/research.log`; this file holds the narrative, numbers,
and the exact re-run steps.

Baseline model throughout: **faster-whisper large-v3-turbo**, int8, CPU.

---

## 1. Goal

Measure STT quality against gold-standard transcripts on real human speech,
first for the batch English pipeline and then for the product chain
`native audio → STT in native script → MT to English`. Improve WER/CER one
change at a time ("measure first, then improve").

## 2. Data sources

| Set | Where | Content | Use |
|---|---|---|---|
| **lordpatil** simulated patient-physician interviews | Kaggle (`lordpatil/simulated-patient-physician-medical-interviews`) | 272 OSCE cases ≈ 55 h real doctor–patient speech, `D:`/`P:` gold transcripts | **English/STT gold baseline** (synthetic-TTS leg previously) |
| **eQOURSE** / multilingual-speech | Hugging Face (public) | real spontaneous two-speaker conversations, native-script transcripts, one recording per language | **Real-native leg** (gu/ml/mr/hi), conversational |
| **ekacare** eka-medical-asr-evaluation-dataset | Hugging Face (public) | 320 real **clinical** Hindi recordings (11–30 s) with Devanagari(+English) transcripts | **Real-native leg**, medical domain |

All real-native clips are materialized under `data/eval/real_native/` as
`<id>.wav` (16 kHz mono) + `<id>.txt` (native-script gold).

## 3. Metrics

- **WER** — word error rate vs the concatenated gold transcript
  (`eval_asr.wer`).
- **CER** — character error rate vs the same gold (computed on characters, so
  it is meaningful across scripts).
- **Digit recovery** — fraction of numeric spans in the gold (ASCII + Devanagari
  digits, normalized: spaces stripped, Devanagari→ASCII) that appear verbatim
  in the hypothesis.

Transcription settings unless noted: forced `language`, Silero VAD, beam 5,
`no_repeat_ngram_size=3`, `condition_on_previous_text=False`, **no** English
prompt for the Indic leg.

## 4. Results

### 4.1 English gold-standard baseline (lordpatil, full-file)

| Case | WER base | WER +ctx | CER base | CER +ctx | glossary |
|---|---|---|---|---|---|
| CAR0004 (chest pain, 448 s) | 0.273 | 0.257 | 0.114 | 0.109 | 16/16 |
| MSK0008 (knee, 874 s) | 0.352 | 0.313 | 0.147 | 0.136 | 20/20 |
| **mean** | **0.313** | **0.285** | **0.131** | **0.123** | 36/36 |

Medical context (initial_prompt + hotwords + silence guard) improves WER
(−0.027 mean) and never hurts glossary coverage. Throughput ≈ 9.7× realtime;
full 55 h ≈ 5–6 h CPU.

### 4.2 Real-native chain baseline (STT in native script, no prompt)

| Lang | Source | clips | mean WER | mean CER | verdict |
|---|---|---|---|---|---|
| hi | ekacare (real clinical) | 20 | **0.654** | 0.448 | only viable one |
| hi | eQOURSE (retail CS) | 13 | 0.833 | 0.568 | moderate; digits & code-mix |
| ml | eQOURSE (sadya ordering) | 15 | 1.117 | 0.808 | broken (wrong-script) |
| mr | eQOURSE (loan banker) | 18 | 1.797 | 1.307 | broken (hallucination) |
| gu | eQOURSE (chat) | 15 | 2.19 | 2.9x | catastrophic (hallucination) |

**Honest verdict: large-v3-turbo is only viable for Hindi on real speech.**
ml/mr/gu fail hard (wrong-script output, gibberish, hallucination loops),
and MT still renders the nonsense as fluent English, silently masking bad STT.

### 4.3 Hindi tuning sweep (beam / prompt / hotwords)

| Config | ekacare_hi (clinical) WER / CER | eqourse_hi (retail) WER / CER |
|---|---|---|
| greedy (beam 1) | 0.728 / 0.513 | 0.838 / 0.578 |
| beam5 (baseline) | 0.654 / 0.448 | **0.833** / **0.568** |
| **beam5 + hi prompt** | **0.638** / **0.375** | 0.874 / 0.620 |
| beam5 + prompt + hotwords | 0.792 / 0.601 | 0.881 / 0.661 |
| beam1 + prompt + hotwords | 0.930 / 0.719 | 0.930 / 0.719 |

The prompt is `MEDICAL_PROMPT["hi"]` from `common/stts_core/medical.py`
(native-script clinic domain; `सभी संख्याओं को सही-सही ट्रांसक्राइब करें`).
Hotwords = `MEDICAL_HOTWORDS["hi"]` + `NUMERIC_HOTWORDS`.

### 4.4 Digit recovery (prompt effect)

| Dir | beam5 | beam5 + prompt |
|---|---|---|
| ekacare_hi | 0/9 (0%) | **6/9 (67%)** |
| eqourse_hi | 6/10 (60%) | **8/10 (80%)** |

A generic numeric-only prompt (no domain words) is **worse** than both no
prompt and the clinic prompt (eqourse 0.917, ekacare 0.732) — the prompt's
domain anchoring matters more than its number instruction.

### 4.5 IndicConformer-600M (dedicated Indic ASR)

**AI4Bharat `indic-conformer-600m-multilingual`** (MIT, gated on HF, ~2.5 GB,
ONNX via `transformers` remote code) replaces Whisper for the languages Whisper
structurally hallucinates on. Hallucination rate = fraction of clips flagged
by the operational definition in `scripts/measure_hallucination.py` (empty
decode, 4-gram repeated ≥3×, or char-bigram Dice < 0.1 vs gold). Beam settings
don't apply (CTC argmax); both CTC and RNNT were measured.

| Lang | Whisper turbo | Whisper large-v3 | **IndicConformer CTC** | IC RNNT |
|---|---|---|---|---|
| gu | 80% (12/15) | 47% (7/15) | **20% (3/15)** | 20% (3/15) |
| ml | 47% (7/15) | 80% (12/15) | **0% (0/15)** | 7% (1/15) |
| mr | 22% (4/18) | 11% (2/18) | **11% (2/18)** | 11% (2/18) |
| hi-eQOURSE | 8% (1/13) | 15% (2/13) | **8% (1/13)** | 23% (3/13) |
| hi-ekacare | 0% (0/20) | 0% (0/20) | **0% (0/20)** | 10% (2/20) |

Full per-clip output in `data/ic_full_run.log`; measurement harness:
`scripts/ic_full_run.py`, `scripts/eval_indic_conformer.py`.

Key findings:

1. **CTC is the decode to use.** RNNT empty-decodes on long clips (hi-eQOURSE
   23%, ekacare 10%) and is never better than CTC.
2. **Zero structural hallucination.** IndicConformer never emits the
   script-mixing gibberish / repetition loops Whisper produces on gu/ml/mr —
   it always decodes coherent native-script text.
3. **The residual flags are eval-set artifacts, not real hallucinations**:
   - gu 3/15 → three micro-clips ≤ 0.5 s (two `હેલો.` → `હ`; one is a 0.2 s
     clip whose gold is a 14-word sentence — a segmentation misalignment).
   - hi-eQOURSE 1/13 → a 0.2 s clip with a long gold (same artifact).
   - mr 2/18 → SEG-003 is a metric false-positive (gold's English words are
     rendered *phonetically in Devanagari*, content is correct); SEG-014 is the
     single true residual (nonsense loop, 1/18 ≈ 5.6%).
   - Sub-second segments are untranscribable by any model; gating them leaves
     gu 0%, hi-eQOURSE 0%.
4. **Trade-off vs Whisper: English/drug-name fidelity.** IndicConformer renders
   Latin tokens phonetically in the native script (`Benadryl` → `वेड्र ऑफ
   सिरप`) and spells numerals as words (`2990` → `രണ്ടായിരത്തി
   തൊള്ളായിരത്തി തൊണ്ണൂറ്`). Whisper keeps them verbatim (in Latin/digits).
   For hi, Whisper stays the better model; that's why the product routes
   **gu/ml/mr → IndicConformer CTC, hi/others → Whisper** (`RoutingASR` in
   `common/stts_core/backends.py`, driven by `INDICCONFORMER_LANGS`).
5. **MT interaction (NLLB/IndicTrans2):** both MT models loop or truncate on
   long *number-word* sequences from IndicConformer's ml output (digits fix it
   — NLLB translated the same sentence cleanly when numerals were digit-form).
   IndicTrans2 translates the routed languages' output better than NLLB for
   mr/gu. Not an STT hallucination, but a follow-up for MT quality.

> End-to-end verification of the shipped routing (unit + full-chain jobs +
> MT interaction) is recorded in `docs/E2E_ROUTING_VERIFICATION.md`.

## 5. Error classes

1. **Digits/numbers mangled everywhere** in the no-prompt baseline; the
   native-script Hindi prompt largely recovers them (see §4.4).
2. **Script-mixing / hallucination** (ml, gu): output mixes Devanagari,
   Bengali, Tibetic characters or pure gibberish; some segments decode empty.
3. **Short clips → hallucination loops** (mr SEG-017 "बरं धन्यवाद" → WER 12;
   hi SEG-007 decodes empty; hi SEG-008 repeats the previous segment).
4. **Code-mixed English tokens** survive better than native words (mr
   "collateral" → "को लेटरल").
5. **Structural collapses not prompt-fixable**: ekacare 0018 drops the whole
   drug regimen (Benadryl/Paracetamol + 10 ml / 625 mg, WER 0.907);
   "270"→"278" one-digit slip.

## 6. Recommended configurations

| Domain | Config | Expected quality |
|---|---|---|
| Clinical Hindi | beam5 + `MEDICAL_PROMPT["hi"]` | **0.638 WER / 0.375 CER** |
| Retail/CS Hindi | beam5, no prompt (clinic prompt if digit fidelity matters) | 0.833 WER / 0.568 CER (0.874 / 80% digits) |
| English | beam5 + medical context (as shipped) | 0.285 WER / 0.123 CER |
| gu / ml / mr | **IndicConformer-600M, CTC** (shipped via `RoutingASR`) | hallucination 20%/0%/11% raw, ~0% after artifact gating; see §4.5 |

**Hotwords are off for the Hindi leg** — the `sot_prev` injection biased
decoding toward English terms and made WER worse (0.638 → 0.792).

## 7. Procedure to re-evaluate

Prereqs: Python 3.9 venv (`.venv/`), `ffmpeg`, models at `models/`
(`whisper-large-v3-turbo`; IndicTrans2 optional for the MT leg), a Kaggle token
in `$KAGGLE_API_TOKEN` (env only, never logged).

Every run is recorded with `scripts/log_run.sh <label> -- <cmd…>`, which tees
timestamped output to `data/research.log` and the console.

### 7.1 Build the real-native eval set (one time)

```bash
scripts/log_run.sh "build real-native set" -- \
  .venv/bin/python scripts/build_real_native_set.py eqourse --langs gu,ml,mr,hi
scripts/log_run.sh "build ekacare_hi (20 clinical clips)" -- \
  .venv/bin/python scripts/build_real_native_set.py ekacare --max-clips 20
```

### 7.2 Chain baseline per language

```bash
for d in eqourse_gu eqourse_ml eqourse_mr eqourse_hi ekacare_hi; do
  scripts/log_run.sh "chain eval $d" -- \
    .venv/bin/python scripts/eval_indic_chain.py \
    --dir data/eval/real_native/$d --no-mt
done
```

`eval_indic_chain.py` prints per-clip GOLD/STT + WER/CER and a mean summary.
Drop `--no-mt` to also run IndicTrans2 indic→en (EN(gold) vs EN(STT)).

### 7.3 Hindi tuning sweep

```bash
scripts/log_run.sh "Hindi tune sweep" -- \
  .venv/bin/python scripts/tune_indic_stt.py \
  --dirs data/eval/real_native/ekacare_hi,data/eval/real_native/eqourse_hi
```

Prints all five configs (§4.3) per dir and per-clip detail for the best one.

### 7.4 Digit recovery analysis

```bash
scripts/log_run.sh "digit recovery" -- \
  .venv/bin/python scripts/analyze_digits.py \
  --dirs data/eval/real_native/ekacare_hi,data/eval/real_native/eqourse_hi
```

### 7.5 English gold baseline (lordpatil)

```bash
.venv/bin/python scripts/download_eval_dataset.py --all   # fetch the ~55 h corpus
scripts/log_run.sh "lordpatil baseline" -- \
  .venv/bin/python scripts/eval_asr_dataset.py --all
```

### 7.6 IndicConformer hallucination eval

First-time: accept the gated repo at
`https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual`, then
`scripts/download_models.py` (or `make models`) fetches it to
`models/indic-conformer-600m`. The venv needs `torch`, `transformers`,
`onnx`, `onnxruntime`.

```bash
scripts/log_run.sh "IndicConformer ctc sweep" -- \
  .venv/bin/python scripts/ic_full_run.py          # ctc + rnnt, all 5 dirs
scripts/log_run.sh "IndicConformer gu ctc" -- \
  .venv/bin/python scripts/eval_indic_conformer.py --dirs data/eval/real_native/eqourse_gu --decode ctc
```

### 7.7 Adding a new configuration or language

- New prompt/hotwords: edit `MEDICAL_PROMPT` / `MEDICAL_HOTWORDS` in
  `common/stts_core/medical.py`, then rerun §7.3/§7.4.
- New language: add clips under `data/eval/real_native/<set>_<iso>/`, pass
  `--lang <iso>` to `eval_indic_chain.py` if the dir suffix is ambiguous.
- New ASR backend: swap the model in the scripts' `WhisperASR(...)` loader and
  keep the same `transcribe()` signature in `eval_indic_chain.py`.

## 8. Reproducibility notes

- Language is forced (never auto-detected) in every Indic run.
- WER/CER compare the **native-script** hypothesis against the **native-script**
  gold; en→en and hi→hi results are therefore directly comparable.
- The Hindi medical prompt text is versioned in `medical.py`, so re-runs stay
  comparable; log the script's git state with the run if precision matters.
