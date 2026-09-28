"""標本設計を、全メッシュ実測に対して机上で当てて選ぶ。

`check_sample_bias.py` が集めたメッシュ別の (棟数, 充填数, 非圧縮サイズ) があれば、
どんな抽出方式でも実測を再走査せずに評価できる。当て推量で設計を決めない。

## 評価する設計

- `top-k`      いまの方式。非圧縮サイズの大きい順にk件。**これが上方に偏る**
- `sys-k`      サイズ順に並べて等間隔にk件（系統抽出）。大中小をまんべんなく引く
- `strat-k`    サイズで4層に分け、各層から均等に引き、**層の総バイト数で重み付けて合成**
- `rand-k`     一様乱択k件（比較用の下限）

## 層化推定の考え方

各メッシュの非圧縮サイズは、zipの中央ディレクトリだけで**ダウンロードせずに**分かる。
そして1棟あたりバイト数は自治体内で安定している（鉾田市で8,000〜11,000）。
つまりサイズは棟数の代理変数として使える。

    推定充填率 = Σ_層 (層の総バイト数 / 全バイト数) × (層の標本の充填率)

これなら「大きいメッシュだけ見て市全体を語る」ことがなくなる。
"""
import json
import pathlib
import random
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRIALS = 400          # 乱数を含む設計は繰り返して分布で見る
STRATA = 4


def log(m):
    print(m, flush=True)


def load(code):
    p = DATA / ("sample_bias_%s.json" % code)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["rows"], d["total"]["fill"]


def pooled(rows):
    """選んだメッシュの建物をまとめて数える（いまの scan.py と同じ）。"""
    n = sum(r["n"] for r in rows)
    return sum(r["ok"] for r in rows) / n if n else None


def stratified(sample_by_stratum, weights):
    """層ごとの充填率を、層の総バイト数の比で合成する。"""
    num = 0.0
    den = 0.0
    for rows, w in zip(sample_by_stratum, weights):
        p = pooled(rows)
        if p is None:
            continue
        num += w * p
        den += w
    return num / den if den else None


def make_strata(rows, k):
    """サイズ順に並べて、総バイト数がなるべく等しくなるようk層に切る。

    件数で等分すると、少数の巨大メッシュが1層に集まって重みが偏る。
    """
    srt = sorted(rows, key=lambda r: -r["bytes"])
    total = sum(r["bytes"] for r in srt)
    strata, cur, acc = [], [], 0.0
    for r in srt:
        cur.append(r)
        acc += r["bytes"]
        if acc >= total / k * (len(strata) + 1) and len(strata) < k - 1:
            strata.append(cur)
            cur = []
    strata.append(cur)
    return [s for s in strata if s]


def design(name, rows, k, rng):
    srt = sorted(rows, key=lambda r: -r["bytes"])
    if name == "top":
        return pooled(srt[:k])
    if name == "sys":
        step = max(1, len(srt) // k)
        return pooled(srt[::step][:k])
    if name == "rand":
        return pooled(rng.sample(srt, min(k, len(srt))))
    if name == "stratsys":
        # 層化＋層内は等間隔（決定的）。乱数を使わないので seed に依存しない。
        # 再現性を主張する作品では、抽出が seed で変わらないほうがよい。
        st = make_strata(rows, STRATA)
        per = max(1, k // len(st))
        picks, ws = [], []
        for sset in st:
            step = max(1, len(sset) // per)
            picks.append(sset[::step][:per])
            ws.append(sum(r["bytes"] for r in sset))
        return stratified(picks, ws)
    if name == "strat":
        st = make_strata(rows, STRATA)
        per = max(1, k // len(st))
        picks, ws = [], []
        for s in st:
            picks.append(rng.sample(s, min(per, len(s))))
            ws.append(sum(r["bytes"] for r in s))
        return stratified(picks, ws)
    raise ValueError(name)


def evaluate(code, rows, truth):
    log("")
    log("=== %s ／ 全数 %.1f%% ／ %d メッシュ・%d 棟 ==="
        % (code, truth * 100, len(rows), sum(r["n"] for r in rows)))
    log("  %-10s %5s %10s %10s %10s" % ("設計", "k", "推定中央値", "誤差中央値", "最悪誤差"))
    out = {}
    for name in ("top", "sys", "rand", "strat", "stratsys"):
        for k in (3, 8, 16, 24):
            rng = random.Random(20260830 + k)
            trials = 1 if name in ("top", "sys", "stratsys") else TRIALS
            est = [design(name, rows, k, rng) for _ in range(trials)]
            est = [e for e in est if e is not None]
            if not est:
                continue
            err = [abs(e - truth) * 100 for e in est]
            out[(name, k)] = (statistics.median(est) * 100,
                              statistics.median(err), max(err))
            log("  %-10s %5d %9.1f%% %9.1fpt %9.1fpt"
                % (name, k, statistics.median(est) * 100,
                   statistics.median(err), max(err)))
    return out


if __name__ == "__main__":
    codes = sys.argv[1:] or [p.stem.split("_")[-1] for p in DATA.glob("sample_bias_*.json")]
    names = json.loads((DATA / "names.json").read_text(encoding="utf-8"))
    allout = {}
    for code in codes:
        got = load(code)
        if not got:
            log("!! %s のメッシュ別データが無い" % code)
            continue
        rows, truth = got
        allout[code] = (evaluate(code, rows, truth), truth)

    if len(allout) < 2:
        sys.exit(0)

    log("")
    log("=== 全都市をまたいだ成績（誤差中央値の平均・最悪）===")
    log("  %-10s %5s %12s %12s" % ("設計", "k", "誤差平均", "全体の最悪"))
    keys = sorted({k for o, _ in allout.values() for k in o})
    rank = []
    for key in keys:
        vals = [o[key] for o, _ in allout.values() if key in o]
        if len(vals) < len(allout):
            continue
        mean_err = statistics.mean(v[1] for v in vals)
        worst = max(v[2] for v in vals)
        rank.append((mean_err, worst, key))
        log("  %-10s %5d %11.1fpt %11.1fpt" % (key[0], key[1], mean_err, worst))
    rank.sort()
    log("")
    log("誤差平均が小さい順の上位5:")
    for mean_err, worst, key in rank[:5]:
        log("  %s k=%d  平均 %.1fpt / 最悪 %.1fpt" % (key[0], key[1], mean_err, worst))
