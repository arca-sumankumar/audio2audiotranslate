#!/usr/bin/env python3
"""MT (translation) evaluation for the product chain, reference-free.

Uses the SHIPPED ASR routing (RoutingASR: gu/ml/mr -> IndicConformer-600M CTC,
hi/others -> faster-whisper turbo) and the real-native eval sets to score the
native -> English translation leg per MT model and per language.

Metrics (no downloads, all computed locally):

  1. **Cascade gap** — WER/CER between EN(gold) and EN(STT). How much STT
     noise passes through MT and changes the translation the customer reads.
     0.0 = the translation of the transcript equals the translation of the
     reference; high values = STT errors propagate into EN.

  2. **Model agreement** — WER/CER between EN(gold) under NLLB vs IndicTrans2.
     When the two models disagree strongly on the reference, the task is hard
     and single-model scores are less trustworthy.

  3. **English-term fidelity** — recall of the Latin-script tokens (medical
     terms, drug names, numerals) embedded in the native-script gold over
     (a) EN(gold)  -> MT reference fidelity
     (b) EN(STT)   -> end-to-end product fidelity
     Words and numbers are scored separately; numbers are digit-normalized
     (dosages like ``625`` / ``10 ml`` are the critical medical payload).

Usage:
    scripts/eval_translation.py                          # all dirs, nllb + indictrans2
    scripts/eval_translation.py --models nllb            # single MT model
    scripts/eval_translation.py --dirs eqourse_gu        # single language
    scripts/eval_translation.py --skip-asr --skip-mt     # re-report cached results only

Results are cached in data/translation_eval.json so STT/MT inference runs once.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from stts_core.config import ModelConfig  # noqa: E402
from stts_core.models import make_asr, make_mt  # noqa: E402

CACHE = os.path.join(ROOT, "data", "translation_eval.json")

DIRS = ["ekacare_hi", "eqourse_hi", "eqourse_gu", "eqourse_ml", "eqourse_mr"]
MODELS = ["nllb", "indictrans2"]

WORD_RE = re.compile(r"[A-Za-z]{2,}")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
PUNCT = re.compile(r"[^\w\s]")
MIN_GOLD_CHARS = 10  # shorter golds are sub-second eval artifacts (garbage MT)


def _wer(ref: str, hyp: str) -> float:
    import eval_asr
    return eval_asr.wer(ref, hyp)


def _cer(ref: str, hyp: str) -> float:
    import eval_indic_chain as chain
    return chain.cer(ref, hyp)


def _norm(s: str) -> str:
    return PUNCT.sub("", s).lower()


def _ascii_digits(s: str) -> str:
    return "".join(chr(48 + int(ch)) if ch.isdecimal() else ch for ch in s)


def _match_norm(text: str, token: str) -> bool:
    """Does the normalized EN output contain the term (word-boundary / digit)?"""
    if not token:
        return False
    if any(ch.isdecimal() for ch in token):
        nd = _ascii_digits(re.sub(r"\D", "", token))
        td = _ascii_digits(re.sub(r"\D", "", text))
        if not nd:
            return False
        return re.search(rf"(?<!\d){re.escape(nd)}(?!\d)", td) is not None
    t = _norm(text)
    n = _norm(token)
    return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t) is not None


class TranslationEval:
    def __init__(self, models: list[str], asr: bool, mt: bool):
        self.models = models
        self.do_asr = asr
        self.do_mt = mt
        self.cache: dict = self._load_cache()
        if asr:
            self.asr = make_asr(ModelConfig(backend="whisper",
                                            offlinePath=os.path.join(ROOT, "models")))
        if mt:
            self.mt = {m: make_mt(ModelConfig(backend=m,
                                              offlinePath=os.path.join(ROOT, "models")))
                       for m in models}
        self.stt_cache: dict = self.cache.setdefault("stt", {})
        self.mt_cache: dict = self.cache.setdefault("mt", {})

    @staticmethod
    def _load_cache() -> dict:
        if os.path.isfile(CACHE):
            try:
                return json.load(open(CACHE, encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_cache(self) -> None:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh, ensure_ascii=False, indent=1)

    def stt(self, lang: str, d: str, cid: str, wav: str) -> str:
        key = f"{lang}/{d}/{cid}"
        if key in self.stt_cache:
            return self.stt_cache[key]
        if not self.do_asr:
            return self.stt_cache.get(key, "(not computed)")
        from stts_core.audio import AudioChunk
        data = open(wav, "rb").read()
        chunk = AudioChunk(seq_no=0, data=data, format="wav",
                           sample_rate=16000, duration_ms=len(data) // 32,
                           is_final=True)
        res = self.asr.transcribe(cid, chunk, batch=True, source_lang=lang)
        text = res[0].text.strip()
        self.stt_cache[key] = text
        return text

    def translate(self, model: str, lang: str, text: str) -> str:
        key = f"{model}/{lang}/{text[:200]}"
        if key in self.mt_cache:
            return self.mt_cache[key]
        if not self.do_mt:
            return "(not computed)"
        out = self.mt[model].translate(lang, "en", text, 0, 0, True)
        out = out.text.strip()
        self.mt_cache[key] = out
        return out

    def terms(self, gold: str) -> tuple[list[str], list[str]]:
        return (list(WORD_RE.findall(gold)), list(NUM_RE.findall(gold)))

    def run_dir(self, d: str) -> dict:
        base = os.path.join(ROOT, "data", "eval", "real_native", d)
        lang = re.search(r"_([a-z]{2})$", d).group(1)
        wavs = sorted(glob.glob(os.path.join(base, "*.wav")))
        lang_key = d
        summary = {m: {"cascade_wer": [], "cascade_cer": [], "excluded": 0,
                       "word_recall_gold": [], "word_recall_stt": [],
                       "num_recall_gold": [], "num_recall_stt": [], "n": 0}
                   for m in self.models + ["__all__"]}
        if len(self.models) >= 2:
            summary["__all__"]["agreement"] = []
        rows = []
        for wav in wavs:
            cid = os.path.splitext(os.path.basename(wav))[0]
            gold = open(os.path.join(base, cid + ".txt"), encoding="utf-8") \
                .read().strip().replace("\n", " ")
            stt = self.stt(lang, d, cid, wav)
            words, nums = self.terms(gold)
            row = {"cid": cid, "lang": lang, "gold": gold, "stt": stt,
                   "models": {}}
            for m in self.models:
                en_gold = self.translate(m, lang, gold)
                en_stt = self.translate(m, lang, stt) if stt else ""
                w = {"en_gold": en_gold, "en_stt": en_stt,
                     "cascade_wer": _wer(en_gold, en_stt) if en_stt else float("nan"),
                     "cascade_cer": _cer(en_gold, en_stt) if en_stt else float("nan"),
                     "word_recall_gold": np.mean([_match_norm(en_gold, t) for t in words]) if words else float("nan"),
                     "word_recall_stt": np.mean([_match_norm(en_stt, t) for t in words]) if words else float("nan"),
                     "num_recall_gold": np.mean([_match_norm(en_gold, n) for n in nums]) if nums else float("nan"),
                     "num_recall_stt": np.mean([_match_norm(en_stt, n) for n in nums]) if nums else float("nan")}
                row["models"][m] = w
                s = summary[m]
                if not np.isnan(w["cascade_wer"]):
                    if len(gold) < MIN_GOLD_CHARS:
                        s["excluded"] += 1
                    else:
                        s["cascade_wer"].append(w["cascade_wer"])
                        s["cascade_cer"].append(w["cascade_cer"])
                for k in ("word_recall_gold", "word_recall_stt",
                          "num_recall_gold", "num_recall_stt"):
                    if not np.isnan(w[k]):
                        s[k].append(w[k])
                s["n"] += 1
            rows.append(row)
            # model agreement on the reference translation
            if len(self.models) >= 2:
                a, b = self.models[0], self.models[1]
                agree = _wer(row["models"][a]["en_gold"], row["models"][b]["en_gold"])
                summary["__all__"]["agreement"].append(agree)
        result = {"lang": lang, "dir": d, "clips": len(wavs), "summary": summary,
                  "rows": rows}
        self.cache.setdefault("results", {})[lang_key] = result
        self._save_cache()
        return result

    def report(self) -> None:
        results = self.cache.get("results", {})
        if not results:
            print("no cached results; run with --skip-asr --skip-mt off first")
            return
        print("=" * 100)
        print("MT EVALUATION SUMMARY (native -> English, shipped ASR routing)")
        print(f"dirs={sorted(results)} models={self.models}")
        print("=" * 100)
        for d in DIRS:
            r = results.get(d)
            if not r:
                continue
            print(f"\n### {d}  ({r['lang']}, {r['clips']} clips)")
            hdr = (f"  {'model':<12} {'cascWER':>8} {'cascCER':>8} "
                   f"{'wRgold':>7} {'wRstt':>7} {'nRgold':>7} {'nRstt':>7} "
                   f"{'exc':>3}")
            print(hdr)
            print("  " + "-" * 68)
            for m in self.models:
                s = r["summary"][m]
                cw = np.mean(s["cascade_wer"]) if s["cascade_wer"] else float("nan")
                cc = np.mean(s["cascade_cer"]) if s["cascade_cer"] else float("nan")
                print(f"  {m:<12} {cw:8.3f} {cc:8.3f} "
                      f"{np.mean(s['word_recall_gold']):7.2f} "
                      f"{np.mean(s['word_recall_stt']):7.2f} "
                      f"{np.mean(s['num_recall_gold']):7.2f} "
                      f"{np.mean(s['num_recall_stt']):7.2f} {s['excluded']:3d}")
            ag = r["summary"]["__all__"].get("agreement")
            if ag:
                print(f"  {'<agreement>':<12} {np.mean(ag):8.3f} "
                      f"(WER between {self.models[0]}-EN(gold) and "
                      f"{self.models[1]}-EN(gold))")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dirs", default=",".join(DIRS))
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--skip-asr", action="store_true", help="reuse cached STT")
    ap.add_argument("--skip-mt", action="store_true", help="reuse cached MT")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
    t0 = time.time()
    ev = TranslationEval(models, asr=not args.skip_asr, mt=not args.skip_mt)
    for d in dirs:
        if d not in DIRS:
            print(f"skipping unknown dir {d!r} (known: {DIRS})")
            continue
        r = ev.run_dir(d)
        print(f"[{d}] done ({r['clips']} clips, {time.time() - t0:.0f}s)",
              flush=True)
    ev.report()


if __name__ == "__main__":
    main()
