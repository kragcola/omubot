#!/usr/bin/env python3
"""P1 feasibility: H1 affective time-series dynamics, human vs ChatGPT (HC3).

Per answer text -> split into sentences -> VADER compound valence per sentence
-> ordered series. Per-text dynamics metrics:
  SD      = std of series (dispersion; STATIC, Sandler-occupied baseline)
  MSSD    = mean of squared successive differences (instability)
  RMSSD   = sqrt(MSSD)
  AR1     = lag-1 autocorrelation (inertia)  [needs >=4 pts]
  ZCR     = zero-crossing rate of mean-centered series (sign flips / (n-1))

Compare human vs AI with: length control (stratify by sentence count),
Mann-Whitney U + Cliff's delta effect size, and a sentence-order NULL test
(shuffle order -> order-dependent metrics' separation should collapse).
"""
from __future__ import annotations
import json, re, glob, random, statistics as st
from pathlib import Path
import numpy as np
from scipy.stats import mannwhitneyu
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

random.seed(17); np.random.seed(17)
VADER = SentimentIntensityAnalyzer()
DATA = "/tmp/p1_hc3"
MIN_SENTS = 4          # min sentences for order-dependent dynamics (AR1 needs >=4)
MAX_PER_DOMAIN = 4000  # cap records per domain for speed

_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])')
def split_sents(text: str) -> list[str]:
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    if not text:
        return []
    parts = _SENT_SPLIT.split(text)
    return [p.strip() for p in parts if len(p.strip()) > 0]

def valence_series(text: str) -> list[float]:
    return [VADER.polarity_scores(s)['compound'] for s in split_sents(text)]

def ar1(x: np.ndarray) -> float | None:
    n = len(x)
    if n < 4:
        return None
    x0, x1 = x[:-1], x[1:]
    if np.std(x0) < 1e-9 or np.std(x1) < 1e-9:
        return 0.0  # flat -> no inertia signal
    return float(np.corrcoef(x0, x1)[0, 1])

def metrics(series: list[float], shuffle: bool = False) -> dict | None:
    if len(series) < MIN_SENTS:
        return None
    x = np.array(series, dtype=float)
    if shuffle:
        x = x.copy(); np.random.shuffle(x)
    d = np.diff(x)
    xc = x - x.mean()
    signs = np.sign(xc); signs[signs == 0] = 1
    zc = int(np.sum(signs[:-1] != signs[1:]))
    return {
        "n": len(x),
        "SD": float(np.std(x, ddof=1)),
        "MSSD": float(np.mean(d**2)),
        "RMSSD": float(np.sqrt(np.mean(d**2))),
        "AR1": ar1(x),
        "ZCR": zc / (len(x) - 1),
    }

def cliffs_delta(a: list[float], b: list[float]) -> float:
    """P(a>b) - P(a<b) via sorted-rank trick; + means a tends larger."""
    a = sorted(a); b = sorted(b)
    i = j = gt = lt = 0
    # count for each a, how many b are smaller / larger
    import bisect
    for v in a:
        lt += bisect.bisect_left(b, v)      # b strictly < v
        gt += len(b) - bisect.bisect_right(b, v)  # b strictly > v
    n = len(a) * len(b)
    return (gt - lt) / n if n else float('nan')  # gt: a>b count... see note

def load_records():
    files = sorted(glob.glob(f"{DATA}/*.jsonl"))
    by_domain = {}
    for fp in files:
        dom = Path(fp).stem
        if dom == "all":
            continue
        rows = []
        with open(fp) as fh:
            for k, line in enumerate(fh):
                if k >= MAX_PER_DOMAIN:
                    break
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        by_domain[dom] = rows
    return by_domain

def collect(by_domain):
    """Return per-text metric dicts tagged human/ai, + shuffled-null variants."""
    out = []
    for dom, rows in by_domain.items():
        for r in rows:
            for txt in (r.get("human_answers") or []):
                m = metrics(valence_series(txt));
                if m: m.update(label="human", dom=dom); out.append(m)
            for txt in (r.get("chatgpt_answers") or []):
                m = metrics(valence_series(txt))
                if m: m.update(label="ai", dom=dom); out.append(m)
    return out

def summarize(rows, key, label):
    vals = [r[key] for r in rows if r["label"] == label and r[key] is not None]
    return vals

def compare(rows, key):
    h = summarize(rows, key, "human"); a = summarize(rows, key, "ai")
    if len(h) < 10 or len(a) < 10:
        return None
    U, p = mannwhitneyu(h, a, alternative="two-sided")
    # Cliff's delta sign: positive => human > ai
    import bisect
    a_s = sorted(a); gt = lt = 0
    for v in h:
        lt += bisect.bisect_left(a_s, v)
        gt += len(a_s) - bisect.bisect_right(a_s, v)
    delta = (gt - lt) / (len(h) * len(a))
    return {
        "metric": key, "n_h": len(h), "n_ai": len(a),
        "median_h": float(np.median(h)), "median_ai": float(np.median(a)),
        "mean_h": float(np.mean(h)), "mean_ai": float(np.mean(a)),
        "cliffs_delta_h_minus_ai": delta, "mannwhitney_p": float(p),
    }

def length_matched(rows, lo, hi):
    return [r for r in rows if lo <= r["n"] <= hi]

if __name__ == "__main__":
    print("Loading HC3 ...")
    bd = load_records()
    for d, r in bd.items():
        print(f"  {d}: {len(r)} records")
    print("Scoring valence + computing dynamics (this takes a bit) ...")
    rows = collect(bd)
    nh = sum(1 for r in rows if r["label"] == "human")
    na = sum(1 for r in rows if r["label"] == "ai")
    print(f"Texts with >={MIN_SENTS} sentences: human={nh}, ai={na}")

    METRICS = ["SD", "MSSD", "RMSSD", "AR1", "ZCR"]
    print("\n=== A. ALL texts (>=4 sentences), human vs AI ===")
    resA = {}
    for k in METRICS:
        c = compare(rows, k); resA[k] = c
        if c:
            print(f"{k:6s} med h={c['median_h']:+.4f} ai={c['median_ai']:+.4f} "
                  f"| Cliff δ(h-ai)={c['cliffs_delta_h_minus_ai']:+.3f} | p={c['mannwhitney_p']:.2e}")

    print("\n=== B. LENGTH-MATCHED (4..8 sentences) ===")
    lm = length_matched(rows, 4, 8)
    nhl = sum(1 for r in lm if r['label']=='human'); nal = sum(1 for r in lm if r['label']=='ai')
    print(f"   n: human={nhl}, ai={nal}")
    resB = {}
    for k in METRICS:
        c = compare(lm, k); resB[k] = c
        if c:
            print(f"{k:6s} med h={c['median_h']:+.4f} ai={c['median_ai']:+.4f} "
                  f"| Cliff δ(h-ai)={c['cliffs_delta_h_minus_ai']:+.3f} | p={c['mannwhitney_p']:.2e}")

    print("\n=== C. NULL TEST: shuffle sentence order (length-matched 4..8) ===")
    null_rows = []
    for dom, rws in bd.items():
        for r in rws:
            for txt in (r.get("human_answers") or []):
                m = metrics(valence_series(txt), shuffle=True)
                if m and 4 <= m["n"] <= 8: m.update(label="human"); null_rows.append(m)
            for txt in (r.get("chatgpt_answers") or []):
                m = metrics(valence_series(txt), shuffle=True)
                if m and 4 <= m["n"] <= 8: m.update(label="ai"); null_rows.append(m)
    resC = {}
    for k in METRICS:
        c = compare(null_rows, k); resC[k] = c
        if c:
            print(f"{k:6s} med h={c['median_h']:+.4f} ai={c['median_ai']:+.4f} "
                  f"| Cliff δ(h-ai)={c['cliffs_delta_h_minus_ai']:+.3f} | p={c['mannwhitney_p']:.2e}")

    out = {"A_all": resA, "B_lenmatched_4_8": resB, "C_null_shuffled": resC,
           "counts": {"human_texts": nh, "ai_texts": na,
                      "lenmatched_human": nhl, "lenmatched_ai": nal}}
    Path("/Volumes/OmubotDisk/omubot/research/affective-divergence/p1/results.json").write_text(
        json.dumps(out, indent=2))
    print("\nSaved results.json")
