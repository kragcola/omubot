#!/usr/bin/env python3
"""P1-bis: H1 affective dynamics on RAID (incl. GPT-4), fixing HC3's stale-model flaw.

Groups: human vs chatgpt(3.5) vs gpt4 -- lets us test the GENERATIONAL question:
does the newer model (gpt4) sit closer to human dynamics than chatgpt?
Domain held constant (abstracts only) to remove genre confound.
Same metrics + null(shuffle) protocol as h1_dynamics.py.
"""
from __future__ import annotations
import json, re, glob, bisect
from pathlib import Path
import numpy as np
from scipy.stats import mannwhitneyu, kruskal
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

np.random.seed(17)
VADER = SentimentIntensityAnalyzer()
DATA = "/tmp/p1_raid"
DOMAIN = "abstracts"      # held constant across all 3 groups
MIN_SENTS = 4
_SENT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])')

def sents(t):
    t = re.sub(r'\s+', ' ', (t or '')).strip()
    return [s.strip() for s in _SENT.split(t) if s.strip()] if t else []

def series(t):
    return [VADER.polarity_scores(s)['compound'] for s in sents(t)]

def ar1(x):
    if len(x) < 4: return None
    a, b = x[:-1], x[1:]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9: return 0.0
    return float(np.corrcoef(a, b)[0, 1])

def metrics(s, shuffle=False):
    if len(s) < MIN_SENTS: return None
    x = np.array(s, float)
    if shuffle: x = x.copy(); np.random.shuffle(x)
    d = np.diff(x); xc = x - x.mean()
    sg = np.sign(xc); sg[sg == 0] = 1
    zc = int(np.sum(sg[:-1] != sg[1:]))
    return {"n": len(x), "SD": float(np.std(x, ddof=1)),
            "MSSD": float(np.mean(d**2)), "RMSSD": float(np.sqrt(np.mean(d**2))),
            "AR1": ar1(x), "ZCR": zc/(len(x)-1)}

def load(group):
    rows = json.load(open(f"{DATA}/{group}.json"))
    return [r for r in rows if r["domain"] == DOMAIN]

def collect(shuffle=False, lo=None, hi=None):
    out = {}
    for g in ("human", "chatgpt", "gpt4"):
        ms = []
        for r in load(g):
            m = metrics(series(r["text"]), shuffle=shuffle)
            if m and (lo is None or lo <= m["n"] <= hi):
                ms.append(m)
        out[g] = ms
    return out

def cliffs(a, b):  # + => a>b
    b = sorted(b); gt = lt = 0
    for v in a:
        lt += bisect.bisect_left(b, v); gt += len(b) - bisect.bisect_right(b, v)
    return (gt - lt) / (len(a) * len(b)) if a and b else float('nan')

def vals(ms, k): return [m[k] for m in ms if m[k] is not None]

def report(data, title):
    print(f"\n=== {title} ===")
    for g in ("human","chatgpt","gpt4"):
        print(f"   {g}: n={len(data[g])}")
    res = {}
    for k in ("SD","MSSD","RMSSD","AR1","ZCR"):
        h, c, g4 = vals(data["human"],k), vals(data["chatgpt"],k), vals(data["gpt4"],k)
        if min(len(h),len(c),len(g4)) < 10: continue
        H, p = kruskal(h, c, g4)
        d_hc = cliffs(h, c); d_hg = cliffs(h, g4); d_cg = cliffs(c, g4)
        _, p_hc = mannwhitneyu(h, c, alternative="two-sided")
        _, p_hg = mannwhitneyu(h, g4, alternative="two-sided")
        res[k] = {"med_human":float(np.median(h)),"med_chatgpt":float(np.median(c)),
                  "med_gpt4":float(np.median(g4)),"kruskal_p":float(p),
                  "cliff_h_vs_chatgpt":d_hc,"cliff_h_vs_gpt4":d_hg,
                  "cliff_chatgpt_vs_gpt4":d_cg,"mwu_p_h_chatgpt":float(p_hc),
                  "mwu_p_h_gpt4":float(p_hg)}
        print(f"{k:5s} med h={np.median(h):+.3f} c3.5={np.median(c):+.3f} gpt4={np.median(g4):+.3f}"
              f" | δ(h-c)={d_hc:+.3f} δ(h-g4)={d_hg:+.3f} δ(c-g4)={d_cg:+.3f} | KW p={p:.1e}")
    return res

if __name__ == "__main__":
    full = collect()
    rA = report(full, "A. abstracts, ALL >=4 sents (human/chatgpt-3.5/gpt4)")
    lm = collect(lo=4, hi=8)
    rB = report(lm, "B. abstracts, LENGTH-MATCHED 4..8 sents")
    nul = collect(shuffle=True, lo=4, hi=8)
    rC = report(nul, "C. NULL shuffle (length-matched 4..8)")
    Path("/Volumes/OmubotDisk/omubot/research/affective-divergence/p1/results_raid.json").write_text(
        json.dumps({"A_all":rA,"B_lenmatched":rB,"C_null":rC,"domain":DOMAIN}, indent=2))
    print("\nSaved results_raid.json")
