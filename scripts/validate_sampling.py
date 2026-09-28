"""新しい標本設計を、全数実測が分かっている12自治体で検証する。

別セッションが `plateau-population/data/gate20.json` に、
12自治体の**全市を読んだ**階数充填率を残している。これが正解。
シミュレーションではなく実物の走査で当てにいく。

`MESH_PER_CITY` を変えて呼べるので、kの決定に使う。

    python scripts/validate_sampling.py 8 16
"""
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scan  # noqa: E402

# 全数実測の正解値。plateau-population/data/gate20.json から同梱（2026-09-28）
TRUTH = ROOT / "data" / "gate20.json"


def log(m):
    print(m, flush=True)


def main(ks):
    truth = {r["code"]: r["storeysFill"] for r in
             json.loads(TRUTH.read_text(encoding="utf-8"))}
    names = json.loads((ROOT / "data" / "names.json").read_text(encoding="utf-8"))
    cov = json.loads((ROOT / "data" / "plateau_coverage.json").read_text(encoding="utf-8"))
    tgt = {c["code"]: c for c in cov["cities"]}
    old = {c["code"]: c for c in
           json.loads((ROOT / "data" / "carte.json").read_text(encoding="utf-8"))["cities"]}

    results = {}
    for k in ks:
        scan.MESH_PER_CITY = k
        log("")
        log("=== k=%d（層あたり %d メッシュ）===" % (k, max(1, k // scan.STRATA)))
        log("  %-10s %8s %8s %8s %8s %6s" % ("自治体", "全数", "旧(top3)", "新(層化)", "旧誤差", "新誤差"))
        errs_new, errs_old, cost, t0 = [], [], 0, time.time()
        for code, tv in truth.items():
            c = tgt[code]
            try:
                r = scan.scan_city(code, c["slug"], c["years"])
            except Exception as e:
                log("  %s 失敗 %s" % (code, type(e).__name__))
                continue
            if r.get("status") != "ok":
                log("  %s -> %s" % (code, r["status"]))
                continue
            new = r["fill"]["storeys"]
            ov = old[code]["fill"]["storeys"]
            errs_new.append(abs(new - tv) * 100)
            errs_old.append(abs(ov - tv) * 100)
            cost += r["nSample"]
            log("  %-10s %7.1f%% %7.1f%% %7.1f%% %7.1fpt %6.1fpt"
                % (names.get(code, code), tv * 100, ov * 100, new * 100,
                   abs(ov - tv) * 100, abs(new - tv) * 100))
        if not errs_new:
            continue
        results[k] = (statistics.mean(errs_new), statistics.median(errs_new),
                      max(errs_new), cost, time.time() - t0)
        log("  %-10s 旧: 平均 %.1fpt / 最悪 %.1fpt" % ("", statistics.mean(errs_old), max(errs_old)))
        log("  %-10s 新: 平均 %.1fpt / 中央値 %.1fpt / 最悪 %.1fpt / 読取 %d棟 / %.1f分"
            % ("", statistics.mean(errs_new), statistics.median(errs_new),
               max(errs_new), cost, (time.time() - t0) / 60))

    if len(results) > 1:
        log("")
        log("=== kの比較 ===")
        log("  %4s %10s %10s %10s %10s" % ("k", "平均誤差", "中央値", "最悪", "読取棟数"))
        for k, (mean, med, worst, cost, el) in sorted(results.items()):
            log("  %4d %9.1fpt %9.1fpt %9.1fpt %10d" % (k, mean, med, worst, cost))


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [8])
