# MT Evaluation Results (native → English translation quality)

Reference-free measurement of the translation leg of the product chain
`native audio → STT in native script → MT to English`, run on the **real-native**
eval sets with the **shipped** ASR routing (`RoutingASR`: gu/ml/mr →
IndicConformer-600M CTC, hi/others → faster-whisper large-v3-turbo beam5).

Raw outputs are cached in `data/translation_eval.json` (STT + per-model
translations); the rerun procedure is §6.

## 1. Goal

The STT leg was previously scored against gold transcripts
([REAL_NATIVE_EVAL.md](REAL_NATIVE_EVAL.md)). This doc quantifies what happens
*after* STT: how much STT noise survives into the English the customer reads,
and which MT model to ship per language. It is deliberately **reference-free**
(no human English gold exists for these sets; see §5).

## 2. Method

For every clip and every MT model (`nllb` = NLLB-200 distilled 600M int8,
`indictrans2` = AI4Bharat IndicTrans2 1.1B):

- **EN(gold)** — translate the native-script gold transcript.
- **EN(STT)** — translate the shipped routing's STT hypothesis.
- **cascade WER/CER** — WER/CER between EN(gold) and EN(STT). 0.0 = the
  translation of the transcript equals the translation of the reference;
  high values = STT errors propagate into English (both STT loss and MT
  compounding/truncation are included).
- **English-term fidelity** — recall of the Latin-script tokens in the gold
  (words and numbers separately) inside EN(gold) (`…gold`, MT reference
  fidelity) and inside EN(STT) (`…stt`, end-to-end product fidelity). Numbers
  are digit-normalized (Unicode digits → ASCII, separators stripped) because
  dosages/prices are the critical payload.
- **model agreement** — WER between NLLB-EN(gold) and IndicTrans2-EN(gold).
  If two independent MT systems disagree wildly on the same input, the task is
  hard and no single-model WER should be read as absolute quality.

Clips whose gold is <10 chars (sub-second eval artifacts, already excluded in
the IC work) are dropped from cascade aggregates (`exc` column). WER/CER are
computed on English token overlap and can exceed 1.0 when the hypothesis is
much longer than the reference.

## 3. Results

### 3.1 Cascade gap + term/number fidelity (product path)

| dir | model | cascWER | cascCER | wordR gold | wordR stt | numR gold | numR stt | exc |
|---|---|---|---|---|---|---|---|---|
| ekacare_hi (clinical) | nllb | 0.931 | 0.706 | 0.70 | 0.50 | 0.25 | 0.25 | 0 |
| ekacare_hi (clinical) | indictrans2 | **0.815** | **0.605** | **0.78** | **0.60** | 0.25 | 0.25 | 0 |
| eqourse_hi (retail) | nllb | 0.929 | 0.612 | **1.00** | 0.55 | – | – | 0 |
| eqourse_hi (retail) | indictrans2 | **0.784** | **0.513** | 0.75 | 0.55 | – | – | 0 |
| eqourse_gu | nllb | **0.819** | **0.630** | 1.00 | 0.52 | **1.00** | **0.50** | 4 |
| eqourse_gu | indictrans2 | 0.911 | 0.711 | 1.00 | **0.55** | 0.00 | 0.00 | 4 |
| eqourse_ml | nllb | 1.062 | 0.772 | 1.00 | **0.00** | 0.14 | 0.00 | 0 |
| eqourse_ml | indictrans2 | 1.230 | 1.090 | 1.00 | **0.00** | 0.25 | 0.00 | 0 |
| eqourse_mr | nllb | **0.867** | **0.679** | 0.69 | 0.42 | 0.25 | **0.28** | 1 |
| eqourse_mr | indictrans2 | 0.937 | 0.692 | **0.94** | **0.69** | **0.33** | 0.08 | 1 |

### 3.2 Model agreement (WER between the two EN(gold) outputs)

| dir | agreement WER |
|---|---|
| ekacare_hi | 1.375 |
| eqourse_hi | 0.662 |
| eqourse_gu | 0.951 |
| eqourse_ml | 1.192 |
| eqourse_mr | 0.740 |

## 4. Findings

1. **The cascade is lossy everywhere** — cascade WER 0.78–1.23 (CER 0.51–1.09)
   across all languages. STT errors propagate through MT, and MT *compounds*
   them (truncation, re-derived numbers). Translation cannot repair upstream
   ASR loss.

2. **MT has bugs independent of STT:**
   - **NLLB truncates longer inputs** — tail content disappears, e.g. an
     ekacare clip whose STT ends in `…augmentin` (the drug) has no mention of
     it in EN; the mr loan sentence truncates to a single clause
     (E2E_ROUTING_VERIFICATION §3).
   - **NLLB emits garbage on some clips** — one ekacare clip →
     `I have to give you Sanskrit. Two Sanskrit days.`
   - **Malayalam number-words re-derive wrong numbers** — IndicConformer
     spells `299` as Malayalam words; NLLB renders it as **`Rs. 2,999`**
     (10× error). In digit form the same input translates cleanly
     (E2E_ROUTING_VERIFICATION §3).

3. **English terms are lost for the routed languages (ml in particular):**
   wordR stt = 0.00 for ml under both MT models. IndicConformer phoneticizes
   inline English (TV, COEP, …) into native script, and MT then translates
   those as native words — inline English terms never reach the English output.

4. **NLLB preserves numerals as digits; IndicTrans2 spells them as words** —
   gu numR gold 1.00 (NLLB, `10 seconds` → `10`) vs 0.00 (IndicTrans2,
   → `a second`). Dosage spans in ekacare survive at only 25% under *both*
   models — a clinically significant gap.

5. **No single MT model wins every language**, and model agreement is low
   (WER 0.66–1.38), so decisions were made by reading outputs as well as
   scores:

   | language | recommendation | basis |
   |---|---|---|
   | hi (both sets) | **IndicTrans2** | lower cascade (0.815/0.784 vs 0.931/0.929), better term retention (0.60/0.55) |
   | gu | **NLLB** | lower cascade (0.819 vs 0.911) and digit-preserving (1.00) |
   | ml | **neither as-is** | number-words break both (cascWER >1, wordR stt 0.00); normalize IC number-words → digits before MT, then NLLB |
   | mr | **mixed** | IndicTrans2 keeps terms better (0.69 vs 0.42) but loses numbers (0.08 vs 0.28) and has higher cascade (0.937 vs 0.867); choose by domain emphasis |

## 5. Limitations

- **No human English reference.** The metrics quantify *change* and *loss*
  (cascade gap, term/number recall) and *disagreement* — not absolute
  translation quality. Building a BLEU reference by MT-translating the golds
  would inherit the reference's own errors (the number-word loops) and is
  undermined by the 0.66–1.38 model-disagreement, so it was not used.
- Short-gold clips (<10 chars) are excluded from cascade aggregates
  (gu 4, mr 1) — they are sub-second eval artifacts that make both MT models
  emit nonsense.
- IndicTrans2 is CPU-slow (~2–10 s/sentence); routing hi→IndicTrans2 in the
  product must weigh streaming latency.

## 6. Procedure to re-evaluate

```bash
.venv/bin/python scripts/eval_translation.py                       # STT + MT fresh (slow)
.venv/bin/python scripts/eval_translation.py --skip-asr --skip-mt  # re-report from cache
# options: --dirs eqourse_gu,ekacare_hi --models nllb,indictrans2
```

- STT/MT outputs are cached in `data/translation_eval.json`; only the cached
  dir needs to exist for `--skip-asr --skip-mt`.
- Raw run log: `data/eval_translation.log`.

## 7. Artifacts

- `scripts/eval_translation.py` — the eval harness (shipped routing + both MT
  models + metrics).
- `data/translation_eval.json` — per-clip STT, EN(gold), EN(STT), metrics.
- `data/eval_translation.log` — full summary output.
- Related: `docs/E2E_ROUTING_VERIFICATION.md` §3 (isolated MT interaction),
  `docs/REAL_NATIVE_EVAL.md` §4.5 (STT side of the routed languages).
