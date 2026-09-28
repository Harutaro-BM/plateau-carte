"""別セッションの指摘「3メッシュ標本は充填率を上方に偏らせる」を独立に検証する。

## 検証した2つの説明

指摘の説明は「非圧縮サイズの大きいメッシュ＝建物が密な市街地中心部＝属性が整備されている」。

対して私は別の機構を疑った。
**属性が入っている建物ほどGMLのバイト数が大きいので、サイズで選ぶこと自体が
属性の入ったメッシュを選ぶことに近いのではないか。**

鉾田市（08234）の全235メッシュを読んで両方を測った結果、
**私の説は外れた**（1棟あたりバイト数と充填率は r=0.041 でほぼ無相関）。
サイズが大きいのは1棟が重いからではなく、単に棟数が多いから。
指摘どおり「密なところが整備されている」が正しい。

このスクリプトは棟数と充填率の相関まで測って、その説明を直接確かめる。
"""
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 同梱の parse_bldg / remote_zip
import parse_bldg as pb  # noqa: E402
import remote_zip as rz  # noqa: E402
import scan  # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "08234"


def log(m):
    print(m, flush=True)


def spearman(a, b):
    """statistics.correlation の method="ranks" はこの Python に無いので自前で順位化する。"""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for j, i in enumerate(order):
            r[i] = j + 1
        return r
    return statistics.correlation(rank(a), rank(b))


def main():
    carte = json.loads((ROOT / "data" / "carte.json").read_text(encoding="utf-8"))
    city = [c for c in carte["cities"] if c["code"] == CODE][0]
    log("=== %s %s（%s年度）===" % (CODE, city["slug"], city["year"]))
    log("carte の値：階数 %.1f%%（%s）／標本 %d棟／メッシュ %s"
        % (city["fill"]["storeys"] * 100, city["grade"]["storeys"],
           city["nSample"], ",".join(city["meshes"])))

    cov = json.loads((ROOT / "data" / "plateau_coverage.json").read_text(encoding="utf-8"))
    tgt = [c for c in cov["cities"] if c["code"] == CODE][0]
    url, year, _ = scan.latest_citygml(CODE, tgt["slug"], tgt["years"])
    log("URL %s" % url.rsplit("/", 1)[-1])

    zf, hf = rz.open_remote(url)
    members = rz.members(zf, "/bldg/")
    sizes = {pb.mesh_of(n): zf.getinfo(n).file_size for n in members}
    log("建物GML %d メッシュ／非圧縮合計 %.1f MB" % (len(sizes), sum(sizes.values()) / 1e6))

    t0 = time.time()
    rows = []
    for i, (mesh, size) in enumerate(sorted(sizes.items(), key=lambda kv: -kv[1]), 1):
        recs, _, _ = pb.parse_city(url, 36.0, 140.5, None, None, {mesh})
        n = len(recs)
        if not n:
            continue
        ok = sum(1 for r in recs if r.get("storeys") and str(r["storeys"]) != "9999")
        rows.append({"mesh": mesh, "bytes": size, "n": n,
                     "bpb": size / n, "fill": ok / n, "ok": ok})
        if i % 25 == 0:
            log("  %3d/%d メッシュ／%.0f 秒" % (i, len(sizes), time.time() - t0))

    tot_n = sum(r["n"] for r in rows)
    tot_ok = sum(r["ok"] for r in rows)
    log("")
    log("全数：%d メッシュ・%d 棟・階数 %.1f%%" % (len(rows), tot_n, tot_ok / tot_n * 100))

    # carte が選んだ3メッシュを再現する
    picked = set(city["meshes"])
    pn = sum(r["n"] for r in rows if r["mesh"] in picked)
    pok = sum(r["ok"] for r in rows if r["mesh"] in picked)
    log("carte標本の再現：%d 棟・階数 %.1f%%（carte記録 %.1f%%）"
        % (pn, pok / pn * 100 if pn else 0, city["fill"]["storeys"] * 100))

    # 途中で落ちても結果を失わないよう、分析の前に保存する
    out = ROOT / "data" / ("sample_bias_%s.json" % CODE)
    out.write_text(json.dumps({"code": CODE, "rows": rows,
                               "total": {"n": tot_n, "ok": tot_ok, "fill": tot_ok / tot_n}},
                              ensure_ascii=False), encoding="utf-8")

    if len(sys.argv) > 2 and sys.argv[2] == "--collect-only":
        log("-> %s / %.1f 分" % (out.name, (time.time() - t0) / 60))
        return

    # 2つの説明を突き合わせる
    big = [r for r in rows if r["n"] >= 30]      # 少数メッシュの雑音を除く
    log("")
    log("--- 何が充填率を決めているか（n≥30 のメッシュ %d件）---" % len(big))
    for label, key in (("1棟あたりバイト数", "bpb"), ("メッシュ内の棟数", "n"),
                       ("非圧縮サイズ", "bytes")):
        x = [r[key] for r in big]
        y = [r["fill"] for r in big]
        log("  %-18s ピアソン %+.3f / スピアマン %+.3f"
            % (label, statistics.correlation(x, y), spearman(x, y)))

    # サイズ順に並べたときの充填率の推移
    log("")
    log("--- 非圧縮サイズ順（上位10と下位10）---")
    log("  %-10s %8s %6s %9s %7s" % ("メッシュ", "bytes", "棟", "B/棟", "階数"))
    srt = sorted(rows, key=lambda r: -r["bytes"])
    for r in srt[:10]:
        log("  %-10s %8d %6d %9.0f %6.1f%%" % (r["mesh"], r["bytes"], r["n"], r["bpb"], r["fill"] * 100))
    log("  ...")
    for r in srt[-10:]:
        log("  %-10s %8d %6d %9.0f %6.1f%%" % (r["mesh"], r["bytes"], r["n"], r["bpb"], r["fill"] * 100))

    # 上位k件を標本にしたときの推定値（kを変えると何が起きるか）
    log("")
    log("--- 上位kメッシュを標本にしたときの階数充填率 ---")
    for k in (1, 3, 5, 10, 20, 50, 100, len(srt)):
        sub = srt[:k]
        n = sum(r["n"] for r in sub)
        o = sum(r["ok"] for r in sub)
        log("  k=%-4d  %6d棟  %5.1f%%" % (k, n, o / n * 100 if n else 0))

    log("")
    log("-> %s / %.1f 分" % (out.name, (time.time() - t0) / 60))


if __name__ == "__main__":
    main()
