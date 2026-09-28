"""案3の追試：震度を入れて測り直す。

前回の測定は計測震度を全域6.25と仮定したため、
公表被害想定の空間変動の支配要因（震度・地盤）を欠いていた。
Fableの指摘どおり、これは「循環」ではなく標準的な単離実験である。
県が閉じているのは建物インベントリ（2013年委託台帳）であり、
震度メッシュはG空間にCC-BYで公開されている。置換対象は建物側だけ。

同時に前回の3つの未検証点を潰す。
  ・(B)構造のみ が (A)建築年×構造 に勝つ逆転は前処理のバグか
  ・町丁字名寄せ 18/35（51%）の減衰は何が落ちているか
  ・棟数相関は規模効果のアーティファクトなので率だけを見る

判定：(A) の率相関が r≧0.7 で生存。届かなければ
県インベントリ（2013年）とPLATEAU（2020年代）の時点不一致＝G5として殺す。
"""
import json
import math
import pathlib
import statistics
import sys

import duckdb
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REFS = ROOT / "refs"
# 付録Eの再検証にだけ使う。令和2年国勢調査の小地域境界（e-Stat、約218MB）は同梱していないので、
# 取得して data/estat/ に置くこと（r2kaXX.shp/.dbf/.prj/.shx）
ESTAT = ROOT / "data" / "estat"

CITY = "22221"
SCENARIO = "L2陸側_津波1_12時"
SHINDO = REFS / "shindo" / "rikugawa.shp"

# 内閣府の全壊率曲線（図から読取）。計測震度ごとの区分別全壊率。
# 木造は建築年6区分、非木造は3区分。5.5以下は0とする。
CURVE_W = {
    5.5: [0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
    6.0: [0.18, 0.14, 0.07, 0.02, 0.01, 0.005],
    6.5: [0.82, 0.72, 0.50, 0.14, 0.07, 0.04],
    7.0: [1.00, 1.00, 0.93, 0.54, 0.37, 0.21],
}
CURVE_N = {
    5.5: [0.00, 0.00, 0.00],
    6.0: [0.04, 0.03, 0.01],
    6.5: [0.25, 0.20, 0.07],
    7.0: [0.56, 0.46, 0.20],
}
# 建築年の区切り（上限年）
BIN_W = [1962, 1971, 1980, 1989, 2001, 9999]
BIN_N = [1971, 1980, 9999]
WOOD = {"601"}


def log(m):
    print(m, flush=True)


def interp(curve, jma, idx):
    ks = sorted(curve)
    if jma <= ks[0]:
        return curve[ks[0]][idx]
    if jma >= ks[-1]:
        return curve[ks[-1]][idx]
    for a, b in zip(ks, ks[1:]):
        if a <= jma <= b:
            t = (jma - a) / (b - a)
            return curve[a][idx] * (1 - t) + curve[b][idx] * t
    return curve[ks[-1]][idx]


def bin_of(year, wood):
    bins = BIN_W if wood else BIN_N
    for i, lim in enumerate(bins):
        if year <= lim:
            return i
    return len(bins) - 1


def rate_full(year, struct, jma):
    """建築年 × 構造 × 震度"""
    wood = struct in WOOD
    i = bin_of(year, wood)
    return interp(CURVE_W if wood else CURVE_N, jma, i)


def rate_nostruct_year(struct, jma):
    """建築年が無い場合：区分の中央（木造は中②、非木造は中）を使う"""
    wood = struct in WOOD
    i = 2 if wood else 1
    return interp(CURVE_W if wood else CURVE_N, jma, i)


def corr(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((v - mx) ** 2 for v in xs))
    sy = math.sqrt(sum((v - my) ** 2 for v in ys))
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


if __name__ == "__main__":
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    log("=" * 76)
    log("■ 震度メッシュの読み込み（静岡県第4次被害想定・陸側ケース、CC-BY）")
    con.execute("CREATE TABLE sh AS SELECT MeshCode, Lon, Lat, JMA_nl AS jma, PL "
                "FROM ST_Read('" + SHINDO.as_posix() + "')")
    n = con.execute("SELECT count(*) FROM sh").fetchone()[0]
    log("  全県 %d メッシュ（250m）" % n)
    # 湖西市の範囲に絞る
    con.execute("CREATE TABLE shk AS SELECT * FROM sh "
                "WHERE Lon BETWEEN 137.42 AND 137.62 AND Lat BETWEEN 34.62 AND 34.82")
    nk = con.execute("SELECT count(*), min(jma), max(jma), avg(jma) FROM shk").fetchone()
    log("  湖西市域 %d メッシュ / 計測震度 %.2f〜%.2f（平均 %.2f）" % (nk[0], nk[1], nk[2], nk[3]))
    if nk[0] < 50:
        log("  !! メッシュが少なすぎる。範囲を確認")
        sys.exit(1)

    log("")
    log("  ※ 前回は全域6.25と仮定していた。実際は %.2f〜%.2f と幅がある。" % (nk[1], nk[2]))

    log("")
    log("=" * 76)
    log("■ 建物への震度の割当（最近傍メッシュ）")
    con.execute("CREATE TABLE b AS SELECT * FROM read_csv_auto('"
                + (DATA / "kosai_bldg.csv").as_posix() + "')")
    nb = con.execute("SELECT count(*) FROM b").fetchone()[0]
    log("  建物 %d 棟" % nb)
    con.execute("""
    CREATE TABLE bj AS
    SELECT b.*, m.jma, m.PL FROM b
    JOIN LATERAL (
      SELECT jma, PL FROM shk
      ORDER BY (Lon-b.lon)*(Lon-b.lon) + (Lat-b.lat)*(Lat-b.lat) LIMIT 1
    ) m ON true
    """)
    st = con.execute("SELECT count(*), min(jma), max(jma), avg(jma) FROM bj").fetchone()
    log("  割当済み %d 棟 / 震度 %.2f〜%.2f（平均 %.2f）" % st)

    rows = con.execute("SELECT lon, lat, year, struct, jma FROM bj").fetchall()
    recs = []
    for lon, lat, year, struct, jma in rows:
        s = str(struct) if struct is not None else "611"
        y = int(year) if year and str(year).isdigit() else None
        recs.append((lon, lat, y, s, float(jma)))

    csvp = DATA / "kosai_weighted.csv"
    with csvp.open("w", encoding="utf-8") as f:
        f.write("lon,lat,wA,wB,wC\n")
        for lon, lat, y, s, jma in recs:
            wA = rate_full(y, s, jma) if y else rate_nostruct_year(s, jma)
            wB = rate_nostruct_year(s, jma)
            # 属性なし：木造の中②を全棟に当てる（構造も分からない場合）
            wC = interp(CURVE_W, jma, 2)
            f.write("%.7f,%.7f,%.6f,%.6f,%.6f\n" % (lon, lat, wA, wB, wC))

    log("")
    log("=" * 76)
    log("■ 町丁字への集計と、名寄せの減衰の内訳")
    shp = ESTAT / "r2ka22.shp"
    con.execute("CREATE TABLE w AS SELECT *, ST_Point(lon,lat) AS pt FROM read_csv_auto('"
                + csvp.as_posix() + "')")
    con.execute("CREATE TABLE ka AS SELECT S_NAME, geom FROM ST_Read('" + shp.as_posix()
                + "') WHERE KEY_CODE LIKE '" + CITY + "%'")
    j = con.execute("""
    SELECT k.S_NAME AS town, count(*) AS n_all,
           sum(w.wA) AS wA, sum(w.wB) AS wB, sum(w.wC) AS wC
    FROM ka k JOIN w ON ST_Within(w.pt, k.geom) GROUP BY 1
    """).df()

    off = pd.read_excel(REFS / "kosai_higai.xls", sheet_name="全壊", header=0)
    off.columns = ["想定区分", "市区町村名", "町丁字名", "揺れ", "液状化",
                   "人工造成地", "津波", "山崖崩れ", "火災", "合計"]
    off = off[off["想定区分"] == SCENARIO].copy()
    off["揺れ"] = pd.to_numeric(off["揺れ"], errors="coerce")
    off = off[["町丁字名", "揺れ"]].dropna()

    # 名寄せの正規化。落ちていた原因は2つ。
    #   ・算用数字 vs 漢数字（「ときわ１丁目」対「ときわ一丁目」）
    #   ・境界側がさらに細分（「新居町新居」対「新居町新居ひばりヶ丘」）
    KAN = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
           "6": "六", "7": "七", "8": "八", "9": "九",
           "１": "一", "２": "二", "３": "三", "４": "四", "５": "五",
           "６": "六", "７": "七", "８": "八", "９": "九"}

    def norm(x):
        x = str(x).strip()
        for a, b in KAN.items():
            x = x.replace(a + "丁目", b + "丁目")
        return x

    off["key"] = off["町丁字名"].map(norm)
    j["key"] = j["town"].map(norm)
    # 境界側が細分されている場合は、公表側の名前を接頭辞に持つものを合算する
    agg = {}
    for _, r in j.iterrows():
        agg[r["key"]] = r
    extra = []
    for _, o in off.iterrows():
        if o["key"] in agg:
            continue
        sub = j[j["key"].str.startswith(o["key"])]
        if len(sub) > 0:
            extra.append({"key": o["key"], "town": o["key"] + "(合算%d)" % len(sub),
                          "n_all": sub["n_all"].sum(), "wA": sub["wA"].sum(),
                          "wB": sub["wB"].sum(), "wC": sub["wC"].sum()})
    if extra:
        j = pd.concat([j, pd.DataFrame(extra)], ignore_index=True)
        log("  境界側の細分を公表側の名前に合算: %d 件" % len(extra))

    m = off.merge(j, on="key", how="outer", indicator=True)
    both = m[m["_merge"] == "both"]
    only_off = m[m["_merge"] == "left_only"]["key"].tolist()
    only_bnd = m[m["_merge"] == "right_only"]["key"].tolist()
    log("  一致 %d / 公表のみ %d / 境界のみ %d" % (len(both), len(only_off), len(only_bnd)))
    log("  公表側で落ちた町丁字: %s" % [x for x in only_off if isinstance(x,str)][:14])
    log("  境界側で落ちた小地域: %s" % only_bnd[:14])
    log("  ※ 公表側は旧町名の大字、境界側は丁目単位に細分されているのが主因と推測される")

    mm = both[both["n_all"] > 30].copy()
    log("  棟数30超に絞る: %d 町丁字（公表の揺れ合計 %.0f 棟）" % (len(mm), mm["揺れ"].sum()))

    log("")
    log("=" * 76)
    log("■ 結果：全壊**率**との相関（震度を入れた）")
    log("")
    mm["off_rate"] = mm["揺れ"] / mm["n_all"]
    out = {}
    log("  %-40s%10s%10s" % ("推定の作り方", "相関 r", "r²"))
    for label, col in (("(A) 建築年 × 構造 × 震度", "wA"),
                       ("(B) 構造 × 震度（建築年なしを模擬）", "wB"),
                       ("(C) 震度のみ（属性なしを模擬）", "wC")):
        mm["r_" + col] = mm[col] / mm["n_all"]
        r = corr(mm["r_" + col].tolist(), mm["off_rate"].tolist())
        log("  %-40s%10.3f%10.3f" % (label, r, r * r))
        out[label] = {"r": round(r, 4), "r2": round(r * r, 4)}

    log("")
    log("  （参考）前回：震度を全域6.25と仮定 → (A) r=0.267 / (B) r=0.472")
    best = max(out.values(), key=lambda v: v["r"])["r"]
    log("")
    if out["(A) 建築年 × 構造 × 震度"]["r"] >= 0.7:
        log("  ✅ 判定：(A) が r≧0.7。**案3は生存。**")
    else:
        log("  ❌ 判定：(A) が r=%.3f で 0.7 に届かない。" % out["(A) 建築年 × 構造 × 震度"]["r"])
        log("     県インベントリ（2013年）とPLATEAU（2020年代）の時点不一致（G5）の可能性。")
    (DATA / "retest.json").write_text(json.dumps({
        "scenario": SCENARIO, "nTowns": len(mm),
        "jmaRange": [nk[1], nk[2]], "result": out,
        "prev": {"A": 0.267, "B": 0.472, "note": "震度を全域6.25と仮定していた"},
        "dropped": {"onlyOfficial": only_off, "onlyBoundary": only_bnd},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("")
    log("-> data/retest.json")
