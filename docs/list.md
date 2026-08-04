# Documentation Map

What each file in `docs/` provides.

| File | Provides |
|---|---|
| [list.md](list.md) | This map — which doc to read for what. |
| [INTEGRATION.md](INTEGRATION.md) | **How to use the product APIs.** Client examples for batch translation (REST) and streaming translation (WebSocket), the downstream WebSocket SDK, the event-envelope reference (message formats/fields), and port list. Start here to call or embed the service. |
| [REAL_NATIVE_EVAL.md](REAL_NATIVE_EVAL.md) | **STT quality on real human speech** (ASR leg). Goal, data sources (lordpatil simulated doctor–patient, eQOURSE real-native gu/ml/mr/hi, ekacare clinical hi), metrics (WER/CER/digit recovery), full result tables including the Hindi prompt/hotword tuning sweep and the IndicConformer-600M comparison (§4.5), error classes, recommended ASR configurations, and the step-by-step re-evaluation procedure. |
| [MT_EVAL.md](MT_EVAL.md) | **Translation quality on the real-native chain** (MT leg). Reference-free evaluation of native → English via the shipped ASR routing (gu/ml/mr → IndicConformer, hi → Whisper): method (cascade-gap, English-term/number fidelity, model agreement), full per-dir × per-model tables, findings (NLLB truncation/garbage, Malayalam number-word errors, ml term loss), per-language MT recommendation (hi → IndicTrans2, gu → NLLB, ml → digit-normalize first), limitations, rerun procedure. |
| [E2E_ROUTING_VERIFICATION.md](E2E_ROUTING_VERIFICATION.md) | **Routing integration verification.** Backend-level unit test proving gu/ml/mr route to IndicConformer (and everything else to Whisper), the three full-chain jobs (ingest → asr → mt → gateway) for the gu/ml/mr demo presets with job IDs, isolated MT-interaction findings (NLLB vs IndicTrans2 on number-words), regression notes, and artifacts. |
| [research-notes.md](research-notes.md) | **Lab notebook / decision trail.** Chronological raw session notes: the simulated doctor–patient STT baseline, real-native Indic-chain baseline, Hindi Whisper tuning, and the reasoning behind each configuration decision (superseded numbers live here; current scored results are in REAL_NATIVE_EVAL.md / MT_EVAL.md). |

Suggested reading order by topic:

- **Use the API / integrate a client** → `INTEGRATION.md`
- **How well does ASR work on Indic audio?** → `REAL_NATIVE_EVAL.md`
- **How well does the full chain translate to English?** → `MT_EVAL.md`
- **Did the ASR routing ship correctly?** → `E2E_ROUTING_VERIFICATION.md`
- **Why were these models/configs chosen?** → `research-notes.md`
