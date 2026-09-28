"""カルテの目玉項目：属性が欠けると下流の推定がどれだけ劣化するか。

湖西市は稀な条件を満たす。
  ・PLATEAUに建築年 80.5%、構造種別 100% が入っている
  ・**町丁字別（35単位）の揺れによる全壊棟数を市がExcelで公表している**
正解と属性が同じ自治体にあるのは、静岡県内でも湖西市くらいだった
（掛川市は町丁字別を公表しているが建築年0%）。

## 測るもの

「PLATEAUで県の被害想定を再現する」ことは目的ではない。
県の想定は固定資産税台帳（全数・正確）で計算されており、こちらは劣ったデータになる。

測るのは **属性の欠落が下流の推定をどれだけ劣化させるか**。
同じ手法で重み付けを3通り変え、公表値との相関がどう変わるかを見る。

  (A) 建築年 × 構造   … 内閣府の全壊率曲線の区分（木造6区分／非木造3区分）
  (B) 構造のみ        … 建築年が無い自治体を模擬（掛川市・東京23区・大阪市など）
  (C) 棟数のみ        … 属性が無い自治体を模擬（更別市など）

これがカルテの主張の実証になる。「属性一覧の○では表せない」ことを数字で示す。

## 全壊率

内閣府「南海トラフの巨大地震 建物被害・人的被害の被害想定項目及び手法の概要」
の全壊率曲線（図）から読み取った値。**図から目視で読んだ概数**である。
相関を見るのが目的なので、絶対値ではなく区分間の相対関係が効く。
"""
import json
import math
import pathlib
import statistics
import sys
import urllib.request
import zipfile

import duckdb
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 同梱の parse_bldg / remote_zip
import parse_bldg as pb  # noqa: E402
import remote_zip as rz  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REFS = ROOT / "refs"
# 付録Eの再検証にだけ使う。令和2年国勢調査の小地域境界（e-Stat、約218MB）は同梱していないので、
# 取得して data/estat/ に置くこと（r2kaXX.shp/.dbf/.prj/.shx）
ESTAT = ROOT / "data" / "estat"
UA = {"User-Agent": "Mozilla/5.0 (plateau-carte/0.1)"}

CITY = "22221"
CITY_NAME = "湖西市"
GML = ("https://assets.cms.plateau.reearth.io/assets/1d/94e107-dc5d-4f80-9bf4-00716a1e5d85/"
       "22221_kosai-shi_city_2023_citygml_2_op.zip")
SCENARIO = "L2陸側_津波1_12時"   # 揺れによる全壊が最大のケース（市合計10,830棟）

# 内閣府の全壊率曲線から読み取った値（計測震度6.25＝震度6強の中央付近）
# 木造は建築年6区分、非木造は3区分
RATE_W = [(1962, 0.57), (1971, 0.46), (1980, 0.20), (1989, 0.05), (2001, 0.02), (9999, 0.01)]
RATE_N = [(1971, 0.11), (1980, 0.09), (9999, 0.03)]
WOOD = {"601"}          # 木造・土蔵造
NONWOOD = {"602", "603", "604", "605", "606", "610"}


def log(m):
    print(m, flush=True)


def rate(year, struct):
    """建築年と構造から全壊率を返す。"""
    tbl = RATE_W if struct in WOOD else RATE_N
    for lim, r in tbl:
        if year <= lim:
            return r
    return tbl[-1][1]


def rate_struct_only(struct):
    """建築年が無い場合。区分の中央値を使う（年代不明なので選べない）。"""
    tbl = RATE_W if struct in WOOD else RATE_N
    return statistics.median([r for _, r in tbl])


def ensure_boundaries(pref="22"):
    shp = ESTAT / ("r2ka" + pref + ".shp")
    if shp.exists():
        return shp
    u = ("https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020"
         "&code=" + pref + "&coordSys=1&format=shape&downloadType=5")
    z = DATA / ("bnd" + pref + ".zip")
    if not z.exists():
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=600) as r:
            z.write_bytes(r.read())
    with zipfile.ZipFile(z) as zz:
        zz.extractall(ESTAT)
    return shp


def load_official():
    df = pd.read_excel(REFS / "kosai_higai.xls", sheet_name="全壊", header=0)
    df.columns = ["想定区分", "市区町村名", "町丁字名", "揺れ", "液状化",
                  "人工造成地", "津波", "山崖崩れ", "火災", "合計"]
    df = df[df["想定区分"] == SCENARIO].copy()
    df["揺れ"] = pd.to_numeric(df["揺れ"], errors="coerce")
    return df[["町丁字名", "揺れ"]].dropna()


def parse_city():
    meshes, _ = rz.open_remote(GML)[0], None
    ms, _ = pb.list_meshes(GML)
    log("  メッシュ %d 件を全部読む" % len(ms))
    recs, fetched, _ = pb.parse_city(GML, 34.7, 137.5, None, log, ms)
    log("  %d 棟 / 転送 %.0f MB" % (len(recs), fetched / 1e6))
    return recs


def corr(xs, ys):
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((v - mx) ** 2 for v in xs))
    sy = math.sqrt(sum((v - my) ** 2 for v in ys))
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


if __name__ == "__main__":
    log("=" * 76)
    log("■ %s：属性の欠落が推定をどれだけ劣化させるか" % CITY_NAME)
    log("  正解＝市が公表する町丁字別の揺れによる全壊棟数（%s）" % SCENARIO)
    log("")
    off = load_official()
    log("  公表値：%d 町丁字 / 揺れによる全壊 合計 %.0f 棟" % (len(off), off["揺れ"].sum()))

    log("")
    log("  PLATEAUを解析中...")
    recs = parse_city()
    kx = math.cos(math.radians(34.7)) * 111320.0

    rows = []
    for r in recs:
        if r.get("cx") is None:
            continue
        y = r.get("year_of_construction")
        y = int(y) if (y or "").isdigit() and 1800 < int(y) < 2100 else None
        st = r.get("structure_type")
        rows.append((137.5 + r["cx"] / kx, 34.7 + r["cy"] / 110540.0, y, st))
    log("  位置を持つ棟 %d" % len(rows))
    nyear = sum(1 for x in rows if x[2])
    nst = sum(1 for x in rows if x[3])
    log("  建築年あり %.1f%% / 構造あり %.1f%%" % (nyear / len(rows) * 100, nst / len(rows) * 100))

    csvp = DATA / "kosai_bldg.csv"
    with csvp.open("w", encoding="utf-8") as f:
        f.write("lon,lat,year,struct,wA,wB\n")
        for lon, lat, y, st in rows:
            s = st or "611"
            # 建築年が無い棟は構造のみの率で埋める。
            # 落として0にすると、建築年欠損の多い町丁字が過小評価され、
            # 「建築年を使うと悪くなる」という人工的な結果になる（最初の実行で起きた）。
            wA = rate(y, s) if y else rate_struct_only(s)
            wB = rate_struct_only(s)
            f.write("%.7f,%.7f,%s,%s,%s,%.4f\n" % (lon, lat, y or "", s, ("%.4f" % wA) if wA != "" else "", wB))

    shp = ensure_boundaries()
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE TABLE b AS SELECT *, ST_Point(lon,lat) AS pt FROM read_csv_auto('"
                + csvp.as_posix() + "')")
    con.execute("CREATE TABLE ka AS SELECT S_NAME, geom FROM ST_Read('" + shp.as_posix()
                + "') WHERE KEY_CODE LIKE '" + CITY + "%'")
    n_ka = con.execute("SELECT count(*) FROM ka").fetchone()[0]
    log("  小地域ポリゴン %d 件（e-Stat 令和2年）" % n_ka)
    j = con.execute("""
    SELECT k.S_NAME AS town, count(*) AS n_all,
           sum(b.wA) AS wA, sum(b.wB) AS wB
    FROM ka k JOIN b ON ST_Within(b.pt, k.geom) GROUP BY 1
    """).df()

    m = off.merge(j, left_on="町丁字名", right_on="town", how="inner")
    log("")
    log("  町丁字名の一致：%d / %d（公表側）" % (len(m), len(off)))
    if len(m) < 8:
        log("  !! 一致が少なすぎる。名称の突合を要調整")
        log("  公表側の例: %s" % list(off["町丁字名"][:8]))
        log("  境界側の例: %s" % list(j["town"][:8]))
        sys.exit(0)

    m = m[m["n_all"] > 30].copy()      # 棟数が少ない町丁字は率が不安定
    log("  棟数30超に絞る: %d 町丁字" % len(m))
    out = {}

    log("")
    log("=" * 76)
    log("■ 結果1：全壊**棟数**との相関（棟数の効果が支配する）")
    log("")
    y = m["揺れ"].tolist()
    log("  %-34s%10s%10s" % ("推定の作り方", "相関 r", "r²"))
    for label, col in (("(A) 建築年 × 構造", "wA"),
                       ("(B) 構造のみ（建築年なしを模擬）", "wB"),
                       ("(C) 棟数のみ（属性なしを模擬）", "n_all")):
        r = corr(m[col].tolist(), y)
        log("  %-34s%10.3f%10.3f" % (label, r, r * r))
        out["count_" + label] = {"r": round(r, 4), "r2": round(r * r, 4)}

    log("")
    log("=" * 76)
    log("■ 結果2：全壊**率**との相関（棟数を割って脆弱性の構成だけを見る）")
    log("  ここが本題。属性が説明できるのは「率」であって「棟数」ではない。")
    log("")
    m["off_rate"] = m["揺れ"] / m["n_all"]
    m["rA"] = m["wA"] / m["n_all"]
    m["rB"] = m["wB"] / m["n_all"]
    yr = m["off_rate"].tolist()
    log("  %-34s%10s%10s" % ("推定の作り方", "相関 r", "r²"))
    for label, col in (("(A) 建築年 × 構造 の平均率", "rA"),
                       ("(B) 構造のみ の平均率", "rB")):
        r = corr(m[col].tolist(), yr)
        log("  %-34s%10.3f%10.3f" % (label, r, r * r))
        out["rate_" + label] = {"r": round(r, 4), "r2": round(r * r, 4)}
    log("  %-34s%10s%10s" % ("(C) 属性なし＝率は一定と仮定", "—", "0.000"))

    log("")
    log("  ※ 全壊率は内閣府の全壊率曲線（図）から読み取った概数。計測震度6.25を全域に仮定。")
    log("     絶対値の再現ではなく、区分間の相対関係が空間分布を説明できるかを見ている。")
    (DATA / "reproduce.json").write_text(json.dumps({
        "city": CITY_NAME, "scenario": SCENARIO, "nTowns": len(m),
        "officialTotal": float(off["揺れ"].sum()), "result": out,
        "note": "全壊率は内閣府の曲線図から読取。計測震度6.25を全域に仮定。相関の比較が目的",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("")
    log("-> data/reproduce.json")
