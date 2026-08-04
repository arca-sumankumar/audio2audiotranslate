# STTS Research Notes

Curated companion to the raw append-only log at `data/research.log`. Raw
command output and timing live there; this file holds the narrative, decisions,
and result tables.

## Goal
Make STTS batch STT quality measurable against a public gold-standard
doctor-patient ASR dataset, then improve WER one step at a time (user: "fix one
problem at a time", "measure first, then improve").

## 2026-08-03 — Session backfill
Prior work this session (details + raw evidence in `data/research.log`):

- **IndicTrans2 integrated** (was HF-gated): `trust_remote_code=True`,
  script-suffixed language tokens (`eng_Latn`, `guj_Gujr`, ...), required
  `<src> <tgt> <text>` input prefix, and `use_cache=False` (transformers 4.57
  KV-cache incompatibility). en→indic emits Devanagari for every Indic target,
  so a new `common/stts_core/indic_translit.py` transliterates to native
  scripts. Verified en→gu/hi/ta/kn/ml/bn/te. Batch E2E job
  `5488c4a2-f5e7-4bca-8dbf-e2043167e4c1` (en→gu, `01_fever.wav`) correct.
- **Known model bug**: ta→en "high fever since last night" → "high blood
  pressure" (greedy and beam 5). Model quality, not our code.
- **Demo streaming UI**: two transcript boxes (#sSrcOut/#sTrOut), no autoplay;
  translated audio accumulates client-side into one playable WAV.
- **README** updated (download/run caveats for IndicTrans2).

### STT focus
`data/test_audio/*.wav` are Piper-TTS syntheses of the `.txt` transcripts
(`scripts/make_test_audio.py`), so the existing `eval_asr.py` WER is not a
gold-standard human-speech measure. We adopt a real doctor-patient dataset.

### Dataset research
| Dataset | Where | Audio | Transcripts | Verdict |
|---|---|---|---|---|
| **Simulated patient-physician medical interviews** (lordpatil) | Kaggle; orig. figshare c.5545842.v1; Sci Data paper | 272 OSCE cases, ~55 h, real human speech | manually corrected, D/P labels | **CHOSEN** (gold standard, real speech) |
| MedDialogue-Audio (Chandanmanvi) | Hugging Face | 147,476 TTS files + noise | MedDialog-EN transcripts | synthetic |
| Medical Speech, Transcription, and Intent (paultimothymooney) | Kaggle | 8.5 h human utterances | symptom clips | short, not dialogues |
| MedDialSpeech (Tomatohust) | Hugging Face | synthetic, overlap stress | dialogue refs | synthetic |
| BeTraC / Synth-DoPaCo (betrac-2026) | Hugging Face | synthetic Opus 16 kHz | dialogs + SOAP | synthetic |

**Decisions**: Kaggle route (lordpatil); full-file WER; smoke test on 1–2
dialogues first; scale to ~55 h only after tuning stabilizes.

### References
- https://www.kaggle.com/datasets/lordpatil/simulated-patient-physician-medical-interviews
- https://doi.org/10.6084/m9.figshare.c.5545842.v1
- https://doi.org/10.1038/s41597-022-01423-1
- https://huggingface.co/datasets/WhissleAI/speech-simulated-medical-exams
- https://huggingface.co/datasets/Chandanmanvi/MedDialog-Audio
- https://www.kaggle.com/datasets/paultimothymooney/medical-speech-transcription-and-intent
- https://huggingface.co/datasets/Tomatohust/meddialspeech
- https://huggingface.co/datasets/BeTraC/betrac-2026

---
*Log format: dated sections per research topic, WER/CER result tables below.*

## 2026-08-03 — STT baseline (Simulated patient-physician interviews)

**Setup**: downloaded `lordpatil` dataset via Kaggle API (auth `Authorization:
Bearer $KAGGLE_API_TOKEN`, new `KGAT_` token). 272 cases = `Data/Audio
Recordings/<ID>.mp3` + `Data/Clean Transcripts/<ID>.txt` (`D:`/`P:` labeled).
New harnesses: `scripts/download_eval_dataset.py` (per-case fetch → `data/eval/
simulated_doctor_patient/<ID>/{audio.mp3,transcript.txt}`) and
`scripts/eval_asr_dataset.py` (mp3→16 kHz WAV via ffmpeg, full-file WER/CER vs
concatenated gold, reuses `eval_asr.py` transcription path + medical context).

### Baseline (faster-whisper large-v3-turbo, int8, beam 5)
| Case | WER base | WER +ctx | CER base | CER +ctx | gloss |
|---|---|---|---|---|---|
| CAR0004 (chest pain, 448 s) | 0.273 | 0.257 | 0.114 | 0.109 | 16/16 |
| MSK0008 (knee, 874 s) | 0.352 | 0.313 | 0.147 | 0.136 | 20/20 |
| **mean** | **0.313** | **0.285** | **0.131** | **0.123** | 36/36 |

Medical context improves WER (−0.027 mean) and never hurts glossary coverage.
Throughput ≈9.7× realtime (model loaded); full 55 h ≈ 5–6 h CPU wall.

### Error classes observed (tuning targets)
1. **Negation inversion** (clinically dangerous): MSK0008 "reflexes **aren't**
   normal" vs gold "reflexes **are** normal".
2. **Medical-term miss**: CAR0004 +ctx mid-file "sharp **vein**" / "a sharp
   thing" vs "sharp **pain**"; varus/valgus section degraded ("reverse stress").
3. **Prefix hallucination**: "Hi, Chris." prepended to MSK0008 hypotheses.
4. **Digits**: 11 digits in gold, 8 missed in both modes (spoken as words).

### Next candidates
Beam 1 vs 5 sweep; temperature/best_of; prompt & hotword tuning; then scale to
full dataset. All runs recorded in `data/research.log` via `scripts/log_run.sh`.

## 2026-08-03 — Real native speech baseline (Indic chain)

Product goal now: real native audio → STT in native script → MT to English.
Built `scripts/build_real_native_set.py` (eQOURSE + ekacare) and
`scripts/eval_indic_chain.py` (forced-language Whisper STT, VAD, beam 5,
`condition_on_previous_text=False`, no English prompt; WER/CER vs native-script
gold; optional IndicTrans2 indic→en).

### Real-native STT baseline (faster-whisper large-v3-turbo, int8, beam 5)
| Lang | Source | clips | mean WER | mean CER | verdict |
|---|---|---|---|---|---|
| hi | ekacare (real clinical) | 20 | **0.654** | 0.448 | best; usable-ish |
| hi | eQOURSE (CS/retail) | 13 | 0.833 | 0.568 | moderate; digits & code-mix |
| ml | eQOURSE (sadya ordering) | 15 | 1.117 | 0.808 | broken (wrong-script) |
| mr | eQOURSE (loan banker) | 18 | 1.797 | 1.307 | broken (hallucination) |
| gu | eQOURSE (chat) | 15 | 2.19 | 2.9x | catastrophic (hallucination) |

**Honest verdict: current ASR is only viable for Hindi.** ml/mr/gu fail hard on
real speech — worse than the synthetic leg. gu→en MT still turns nonsense into
fluent English ("Open today, so that you can enjoy the festive atmosphere"),
which would silently mask bad STT in the product.

### Error classes
1. **Digits/numbers mangled everywhere**: mg doses, mL, ₹ amounts, pincode,
   10 AM, "2990/1895/800". ekacare: Augmentin dose, CBC values wrong.
2. **Script-mixing / hallucination** (ml, gu): output mixes Devanagari, Bengali,
   Tibetic chars, or pure gibberish; some SEGs empty.
3. **Short clips → hallucination loops**: mr SEG-017 ("बरं धन्यवाद" → 12 WER);
   hi SEG-008 emitted the *previous* segment's text (carry-over).
4. **Code-mixed English tokens** survive better than native words (mr "collateral"
   → "को लेटरल", "साठी लागत" → "साथ लाग").

### Next candidates
- Tune Whisper for hi first (most viable): native-script `initial_prompt`,
  hotwords, beam/temp sweep — verify whether ekacare digits recover.
- For ml/mr/gu: whisper-lv3-turbo is too weak; test dedicated Indic ASR
  (IndicConformer / IndicASR, or full large-v3 non-turbo), or fine-tune.
  Hallucination-dominated errors hint prompt suppression may help most on gu/mr.
- Decide product STT gate per language (hi only today?).

## 2026-08-03 — Hindi Whisper tuning (real native audio)

Swept beam / `initial_prompt` / hotwords via `scripts/tune_indic_stt.py`
(same transcription path as the chain eval) and quantified digit recovery with
`scripts/analyze_digits.py`.

### Config sweep (faster-whisper large-v3-turbo, int8)
| Config | ekacare_hi (clinical) | eqourse_hi (retail) |
|---|---|---|
| greedy | 0.728 / 0.513 | 0.838 / 0.578 |
| beam5 (baseline) | 0.654 / 0.448 | **0.833** / 0.568 |
| beam5 + hi prompt | **0.638** / **0.375** | 0.874 / 0.620 |
| beam5 + prompt + hotwords | 0.792 / 0.601 | 0.881 / 0.661 |
| beam1 + prompt + hotwords | 0.930 / 0.719 | 0.930 / 0.719 |

WER / CER (mean over clips).

### Findings
1. **Native-script medical prompt helps clinical Hindi** (WER −0.016, CER
   −0.073) and is **domain-sensitive**: the clinic-domain prompt slightly
   hurts retail WER (0.833 → 0.874) but still helps retail digits.
2. **Hotwords actively hurt** on hi (0.638 → 0.792 clinical): the `sot_prev`
   injection biases decoding toward English terms. Recommendation: do not use
   hotwords for the Hindi ASR leg.
3. **Digits recover strongly with the prompt** (`सभी संख्याओं को सही-सही
   ट्रांसक्राइब करें`):
   | Dir | beam5 | beam5+prompt |
   |---|---|---|
   | ekacare_hi | 0/9 (0%) | **6/9 (67%)** |
   | eqourse_hi | 6/10 (60%) | **8/10 (80%)** |
4. Remaining failures are structural, not prompt-fixable: clip 0018 collapses
   entirely (drug names + 10 ml / 625 mg dropped, WER 0.907); SEG-005 "270"→
   "278" (one-digit slip); SEG-007 decodes empty (VAD).
5. A generic numeric-only prompt (no domain words) is **worse** than both no
   prompt and the clinic prompt (eqourse 0.917, ekacare 0.732) — the prompt's
   domain anchoring matters more than its number instruction.

### Recommended Hindi config
- **Clinical audio**: beam5 + `MEDICAL_PROMPT["hi"]` → **0.638 WER / 0.375 CER**.
- **Retail/CS audio**: beam5, no prompt → 0.833 WER / 0.568 CER (use clinic
  prompt only if digit fidelity is the priority: 0.874 WER / 80% digits).
- **Hotwords off** for the Hindi leg (they hurt).

### Implication
Hindi real-native STT is now at ~0.64 WER / 0.375 CER (clinical) with the
prompt; a retail/CS domain could use a domain-matched (non-clinic) prompt to
keep WER low while retaining digit gains. ml/mr/gu remain broken and need a
different model — see next phase.
