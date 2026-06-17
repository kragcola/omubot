#!/usr/bin/env python3
"""P2 offline Chinese affective-dynamics annotation over topic_corpus.db.

Reads the research side-channel db produced by services/group/corpus_capture.py,
rebuilds per-(block, speaker) valence time series, computes the H1/H2 metrics
from the P0 reading notes, and compares human vs AI with effect sizes + a
sentence-order null test. Pure offline; never touches the bot.

H1 (temporal dynamics): SD / MSSD / RMSSD / AR1 / ZCR over the valence series.
H2 (compensation):
  - MIN co-activation: per-message min(pos_words, neg_words) via cnsenti (DUTIR).
  - softener-wrap rate: P(negative-valence message carries a tone-softener:
    sentence-final bracket / ~ / 称呼语 / 削弱 emoji) -- Song 2022 / Li&Lin 2023.

Valence backends (both dictionary-based, offline):
  - snownlp: continuous sentiment 0..1 -> mapped to [-1, 1].
  - cnsenti (DUTIR 大连情感本体): pos/neg word counts -> MIN co-activation.

Usage:
  .venv/bin/python annotate_corpus.py [--db PATH] [--min-msgs 4] [--report out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore", category=SyntaxWarning)

import numpy as np  # noqa: E402
from snownlp import SnowNLP  # noqa: E402

try:
    from cnsenti import Sentiment as _CnSentiment
    _CNS = _CnSentiment()
except Exception:  # pragma: no cover
    _CNS = None

# ── Chinese sentence splitting ────────────────────────────────────────────
_ZH_SENT = re.compile(r'[^。！？!?\n…]+(?:[。！？!?…]+|$)')

def split_zh(text: str) -> list[str]:
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    if not text:
        return []
    parts = [s.strip() for s in _ZH_SENT.findall(text)]
    return [s for s in parts if s]

# ── valence per sentence: snownlp 0..1 -> [-1,1] ──────────────────────────
def valence(sentence: str) -> float:
    s = sentence.strip()
    if not s:
        return 0.0
    try:
        return float(SnowNLP(s).sentiments) * 2.0 - 1.0
    except Exception:
        return 0.0

# ── tone-softener detection (H2): bracket / tilde / endearment / weakening ─
_SOFTENER_TAIL = re.compile(r'[（(][^）)]{0,6}[）)]?\s*$|[~～]+\s*$|[。.]{3,}\s*$')
_ENDEARMENT = ("亲爱的", "宝", "宝贝", "亲", "哥哥", "姐姐", "呀", "啦", "嘛", "呢", "哈", "嘿嘿", "嘻嘻")
_WEAKEN_KAOMOJI = ("(小声", "（小声", "(bushi", "（不是", "(doge", "(狗头", "(笑", "（笑")

def has_softener(text: str) -> bool:
    t = (text or "").rstrip()
    if not t:
        return False
    if _SOFTENER_TAIL.search(t):
        return True
    if any(w in t for w in _WEAKEN_KAOMOJI):
        return True
    return any(t.endswith(e) for e in _ENDEARMENT)

# ── co-activation via cnsenti DUTIR pos/neg word counts ───────────────────
def coactivation_min(text: str) -> float:
    """min(pos_words, neg_words): both valences present in one message (Larsen MIN)."""
    if _CNS is None or not text:
        return 0.0
    try:
        r = _CNS.sentiment_count(text)
        return float(min(r.get("pos", 0), r.get("neg", 0)))
    except Exception:
        return 0.0

# ── H1 dynamics metrics over a valence series ─────────────────────────────
def ar1(x: np.ndarray) -> float | None:
    if len(x) < 4:
        return None
    a, b = x[:-1], x[1:]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])

def h1_metrics(series: list[float], shuffle: bool = False) -> dict | None:
    if len(series) < 4:
        return None
    x = np.array(series, float)
    if shuffle:
        x = x.copy()
        np.random.shuffle(x)
    d = np.diff(x)
    xc = x - x.mean()
    sg = np.sign(xc)
    sg[sg == 0] = 1
    zc = int(np.sum(sg[:-1] != sg[1:]))
    return {
        "n": len(x),
        "SD": float(np.std(x, ddof=1)),
        "MSSD": float(np.mean(d ** 2)),
        "RMSSD": float(np.sqrt(np.mean(d ** 2))),
        "AR1": ar1(x),
        "ZCR": zc / (len(x) - 1),
    }

# ── rebuild per-(block, speaker) unit from corpus ─────────────────────────
def load_units(db_path: str, min_msgs: int):
    """One analysis unit = one speaker's ordered messages within one topic block.
    Returns list of dicts: {role, valence_series, min_series, neg_msgs, neg_softened}."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT group_id, block_id, speaker, role, text, message_id, captured_at "
        "FROM topic_corpus ORDER BY group_id, block_id, speaker, captured_at, message_id"
    ).fetchall()
    con.close()

    grouped: dict[tuple, list] = defaultdict(list)
    for g, b, sp, role, text, _mid, _ts in rows:
        grouped[(g, b, sp, role)].append(text or "")

    units = []
    for (g, b, sp, role), texts in grouped.items():  # noqa: B007  (g,b,sp for key only)
        # valence series = one value per sentence across the speaker's messages in this block
        vseries: list[float] = []
        min_vals: list[float] = []
        neg_msgs = neg_softened = 0
        for t in texts:
            sents = split_zh(t)
            for s in sents:
                vseries.append(valence(s))
            # H2 per-message: co-activation + softener-on-negative
            mv = coactivation_min(t)
            min_vals.append(mv)
            msg_val = np.mean([valence(s) for s in sents]) if sents else 0.0
            if msg_val < -0.15:  # negative message
                neg_msgs += 1
                if has_softener(t):
                    neg_softened += 1
        if len(vseries) < min_msgs:
            continue
        units.append({
            "role": role, "group": g, "block": b,
            "valence_series": vseries,
            "min_coactivation": float(np.mean(min_vals)) if min_vals else 0.0,
            "neg_msgs": neg_msgs, "neg_softened": neg_softened,
        })
    return units

# ── effect size + comparison ──────────────────────────────────────────────
def cliffs_delta(a: list[float], b: list[float]) -> float:
    import bisect
    b_s = sorted(b)
    gt = lt = 0
    for v in a:
        lt += bisect.bisect_left(b_s, v)
        gt += len(b_s) - bisect.bisect_right(b_s, v)
    return (gt - lt) / (len(a) * len(b)) if a and b else float("nan")

def compare(units, key, shuffle=False):
    from scipy.stats import mannwhitneyu
    h, a = [], []
    for u in units:
        m = h1_metrics(u["valence_series"], shuffle=shuffle)
        if m is None or m[key] is None:
            continue
        (h if u["role"] == "human" else a).append(m[key])
    if len(h) < 5 or len(a) < 5:
        return None
    U, p = mannwhitneyu(h, a, alternative="two-sided")
    del U
    return {"metric": key, "n_h": len(h), "n_ai": len(a),
            "median_h": float(np.median(h)), "median_ai": float(np.median(a)),
            "cliffs_delta_h_minus_ai": cliffs_delta(h, a), "mannwhitney_p": float(p)}

def compare_h2(units):
    """H2: co-activation MIN + softener-on-negative rate, human vs AI."""
    from scipy.stats import mannwhitneyu
    out = {}
    h_min = [u["min_coactivation"] for u in units if u["role"] == "human"]
    a_min = [u["min_coactivation"] for u in units if u["role"] == "ai"]
    if len(h_min) >= 5 and len(a_min) >= 5:
        _, p = mannwhitneyu(h_min, a_min, alternative="two-sided")
        out["MIN_coactivation"] = {"median_h": float(np.median(h_min)),
            "median_ai": float(np.median(a_min)),
            "cliffs_delta_h_minus_ai": cliffs_delta(h_min, a_min), "mannwhitney_p": float(p)}
    for role in ("human", "ai"):
        neg = sum(u["neg_msgs"] for u in units if u["role"] == role)
        soft = sum(u["neg_softened"] for u in units if u["role"] == role)
        out[f"softener_on_neg_rate_{role}"] = (soft / neg) if neg else None
        out[f"neg_msgs_{role}"] = neg
    return out

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="../../../storage/topic_corpus.db")
    ap.add_argument("--min-msgs", type=int, default=4)
    ap.add_argument("--report", default="results_p2.json")
    args = ap.parse_args()
    np.random.seed(17)

    units = load_units(args.db, args.min_msgs)
    nh = sum(1 for u in units if u["role"] == "human")
    na = sum(1 for u in units if u["role"] == "ai")
    print(f"units: human={nh}, ai={na} (min sentences/unit={args.min_msgs})")
    if nh < 5 or na < 5:
        print("NOT ENOUGH DATA yet -- enable corpus_capture and let it accumulate.")
        with open(args.report, "w") as fh:
            json.dump({"status": "insufficient_data", "n_human": nh, "n_ai": na}, fh, indent=2)
        return

    res = {"counts": {"human": nh, "ai": na}, "H1": {}, "H1_null": {}, "H2": {}}
    print("\n=== H1 dynamics (human vs AI) ===")
    for k in ("SD", "MSSD", "RMSSD", "AR1", "ZCR"):
        c = compare(units, k)
        res["H1"][k] = c
        if c:
            print(f"{k:5s} med h={c['median_h']:+.3f} ai={c['median_ai']:+.3f} "
                  f"| δ(h-ai)={c['cliffs_delta_h_minus_ai']:+.3f} | p={c['mannwhitney_p']:.2e}")
    print("\n=== H1 NULL (shuffle sentence order) ===")
    for k in ("SD", "MSSD", "AR1", "ZCR"):
        c = compare(units, k, shuffle=True)
        res["H1_null"][k] = c
        if c:
            print(f"{k:5s} δ(h-ai)={c['cliffs_delta_h_minus_ai']:+.3f} | p={c['mannwhitney_p']:.2e}")
    print("\n=== H2 compensation (co-activation + softener-on-negative) ===")
    res["H2"] = compare_h2(units)
    print(json.dumps(res["H2"], ensure_ascii=False, indent=2))

    with open(args.report, "w") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print(f"\nSaved {args.report}")

if __name__ == "__main__":
    main()
