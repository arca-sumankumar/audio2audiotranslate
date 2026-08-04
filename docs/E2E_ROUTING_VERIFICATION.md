# End-to-End Routing Verification (IndicConformer gu/ml/mr)

Results of the production wiring for the hallucination work: `RoutingASR`
(`common/stts_core/backends.py`) sends **gu/ml/mr → IndicConformer-600M (CTC)**
and everything else → faster-whisper large-v3-turbo. `make_asr()` returns the
facade; the ASR worker code is unchanged.

Environment: real stack (`make local-real`), ASR worker restarted with the
routing code, offlinePath `models/`. All clips are real recorded speech at
16 kHz mono.

---

## 1. Routing unit test (backend level, batch)

`make_asr(ModelConfig(backend="whisper", offlinePath="models"))` driven
directly with full-WAV `AudioChunk`s. Confirms dispatch and output script.

| source | clip | routed to | STT output |
|---|---|---|---|
| gu | eqourse_gu/SEG-012 | IndicConformer | `થઈ ગયો હવે ટીવી પર રેડ લઇટ લિંક થશે રિમોટ ઓટોમેટિકલી કનેક્ટ થશે ટ્રાય કરો વોલ્યુમ વધારો` |
| ml | eqourse_ml/SEG-007 | IndicConformer | `ആ അഞ്ചു ലിറ്റർ അധികം പാലട പ്ര്രഥമന് എണ്ണൂറ് രൂപ...` (native script, correct) |
| mr | eqourse_mr/SEG-003 | IndicConformer | `फस्ट इयर दोन लाख शी थाउंड...` (English words phonetically in Devanagari) |
| hi | ekacare_hi/0001 | **Whisper** | `कि यह जो हुआ है आपको इंफेक्शन हूआ... मेडिकेशिन जुह है` (keeps English in Latin) |

Verdict: dispatch is correct; hi keeps Whisper (Latin English/drug-name
fidelity), gu/ml/mr get coherent native-script text.

## 2. Full-chain jobs (ingest → asr → mt → gateway)

Three batch jobs submitted via `POST /api/v1/translate` (source lang gu/ml/mr,
target en, MT model `nllb`). STT outputs below match the `data/ic_full_run.log`
CTC run; MT text is the gateway job's `transcript`.

### 2.1 gu — `19_gu_tvremote` (eQOURSE TV-remote call)

- GOLD: `હા હેલો, ભાઈ જો ને યાર આ મારું TV રિમોટ કામ નથી કરતું. MI TV બધું જ બટન દબાવું છું પણ કંઈ જ નથી થતું.`
- STT (IndicConformer CTC): `હા હાલો ભાઈ જો ને યાર આ મારું ટીવી રિમોન કામ નથી કરતું આઈ ટીવી બધું જ બટન દબાવું છું પણ કંઈ જ નથી થતું`
- MT (NLLB) → job `baf43c5d…`:
  `Hello brother Jon, this is my TV rim doesn't work. My TV is all I press but nothing happens.`
- Verdict: **pass** — clean STT, no gibberish (was `80%` hallucinated under Whisper), MT comprehensible.

### 2.2 ml — `20_ml_sadhya` (eQOURSE sadhya-ordering call)

- GOLD: `5 ലിറ്റർ അധികം പാലട പ്രഥമന് 800 രൂപ അധികം വരും. സദ്യയ്ക്ക് 2990 രൂപയും പാലടയ്ക്ക് 800-ഉം ചേർത്ത് ആകെ 3790 രൂപ വരും.`
- STT (IndicConformer CTC): `ആ അഞ്ചു ലിറ്റർ അധികം പാലട പ്ര്രഥമന് എണ്ണൂറ് രൂപ അധികം വരും സദ്യക്ക് രണ്ടായിരത്തി തൊള്ളായിരത്തി തൊണ്ണൂറ് രൂപയും പാലടക്ക് എണ്ണൂറും ചേർത്ത് ആകെ മൂവായിരത്തി എഴുന്നൂറ്റി തൊണ്ണൂറ് രൂപ വരും`
- MT (NLLB) → job `b4992641…`:
  `The first one will get more than Rs 800 for the first one, and the second one will get Rs 2,900 ... plus the second one Rs 9,00` (… NLLB **repetition loop**)
- Verdict: **STT pass** (correct content, was `47%` hallucinated under Whisper),
  **MT fail** — NLLB loops on the long Malayalam *number-word* sequence.

### 2.3 mr — `21_mr_educationloan` (eQOURSE loan-banker call)

- GOLD: `मैडम, माझ्या मुलाला engineering ला admission झालंय. COEP पुणे मध्ये. computer science. fees बहुत आहे, education loan मिळेल का?`
- STT (IndicConformer CTC): `मॅडम माझ्या मुलाला इंजिनिअरिंगला ॲडमिशन झालंय सीओई पी पुणेमध्ये कम्प्युटर सायन्स फीस बहुत आहे ॲज्युकेशन लोन मिळेल का`
- MT (NLLB) → job `2ca3239e…`:
  `Madam, my son is an engineering graduate.`
- Verdict: **STT pass** (was `22%` hallucinated under Whisper), **MT weak** — NLLB
  truncates to the first clause and drops COEP / fees / loan.

## 3. MT interaction findings (isolated, backend-level)

Same STT strings fed to each MT model directly:

| STT | NLLB (nllb) | IndicTrans2 (indictrans2) |
|---|---|---|
| mr loan | truncates to `Madam, my son is an engineering graduate.` | **`Madam My son is admitted in Engineering COEP Computer Science fees are very high in Pune Can I get an education loan?`** (full + correct) |
| gu tvremote | `Hello brother Jon, this is my TV rim doesn't work. My TV is all I press but nothing happens.` | `Now first of all see when this is my TV remote not working all these TVs are pressing the same button but there is nothing.` |
| ml sadhya (number-words) | repetition loop | repetition loop (`thali thali thali…`) |
| ml sadhya (**digits** form) | **`The five litres of pallet will cost more than Rs 800 per first, Rs 2990 per second and Rs 3790 for 800 pallets.`** (clean) | `That five litre unit is Palta basic 800 Droop unit…` |

Conclusions:
- IndicConformer renders numerals as **words** (`2990` → `രണ്ടായിരത്തി തൊള്ളായിരത്തി തൊണ്ണൂറ്`);
  long number-word sequences make **both** MT models loop. In digit form the
  same sentence translates cleanly.
- For the routed languages' output, **IndicTrans2 > NLLB** on mr and gu; ml is
  MT-broken on number-words either way.
- These are MT-quality issues surfaced by the ASR routing, not STT
  hallucinations. Candidate follow-ups: per-language MT routing (gu/ml/mr →
  IndicTrans2) or number-word→digit normalization before MT. The full MT eval
  quantifies these per language and per model in
  [MT_EVAL.md](MT_EVAL.md) (recommendation: hi→IndicTrans2, gu→NLLB,
  ml→digit-normalize first).

## 4. Regression

- `make_asr(ModelConfig(backend="mock"))` still returns `MockASR` and emits
  deterministic mock transcripts (no backend imports on the mock path).
- Whisper streaming/batch behavior for hi/other languages is unchanged (same
  `WhisperASR` instance, same decode options).

## 5. Artifacts

- `scripts/ic_full_run.py` — ctc+rnnt sweep over all 5 real-native dirs
- `scripts/eval_indic_conformer.py` — per-dir/harness eval, `--decode ctc|rnnt`
- `data/ic_full_run.log` — raw per-clip output for the sweep
- `data/research.log` — run records
- `docs/REAL_NATIVE_EVAL.md` §4.5 — hallucination-rate comparison table
- Demo presets: `19_gu_tvremote`, `20_ml_sadhya`, `21_mr_educationloan` (wav+txt)
