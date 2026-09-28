"""カルテの結果を日本地図にする。

7枚目で示した「建築年が入る自治体は県単位で固まる」は、表では伝わらない。
県境が見える地図に自治体を塗ると、色が県境で切り替わるのが一目で分かる。

## 設計

- 背景：47都道府県の輪郭（N03を県単位で融合）。**県境を見せるのが目的**なので必須
- 前景：PLATEAUがある306自治体のうち305を段階色で塗る
  （小笠原村 13421 は下記の窓から外れるので描かれない。地図上の件数は305）
- 政令市：PLATEAUは 22100（静岡市）のように区を持たないコードを使うが、
  N03は 22101（葵区）のように区で分かれる。**先頭3桁一致で集約する**
- 島の扱い：小笠原・南鳥島・沖ノ鳥島は本土の縮尺を壊すので落とす。
  沖縄は本土の左下に別枠（インセット）で置く

出力は依存ライブラリ無しで使える素のSVG。ビューアにも動画にも同じものを使う。
"""
import json
import math
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 同梱の parse_bldg / remote_zip
import remote_zip as rz  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "n03"
N03 = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2025/N03-20250101_%02d_GML.zip"

# 描画する窓。ここから外れた島は落とす（小笠原・南鳥島・沖ノ鳥島）
MAIN = (128.3, 30.0, 146.2, 45.6)      # lon0, lat0, lon1, lat1
OKI = (122.9, 24.0, 128.4, 26.9)

# 段階色。ビューアの凡例と同じ意味にする
COLORS = {"◎": "#1f7a4d", "○": "#7fb069", "△": "#e0a83a", "▲": "#d4762a", "×": "#c94f4f"}
GRAY_FILL = "#e8e6e1"
GRAY_LINE = "#c9c5bd"

W = 1000        # 出力SVGの幅
# 幅1000pxで日本全体（経度約18度）を描くと 1px ≒ 0.018度。
# それより細かい頂点は見えないので、その手前まで単純化する。
SIMP_PREF = 0.012   # 県輪郭の単純化（度）
SIMP_CITY = 0.005   # 自治体の単純化（度）
# 描いても点にしかならない島を落とす閾値（外接矩形の面積・平方度）
MIN_RING_PREF = 0.0006
MIN_RING_CITY = 0.0002


def log(m):
    print(m, flush=True)


def fetch_geojson(pref):
    """N03のzipからgeojsonメンバだけHTTPレンジ要求で取る（1県あたり約4.5MB転送）。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / ("n03_%02d.geojson" % pref)
    if out.exists() and out.stat().st_size > 1000:
        return out, 0
    zf, hf = rz.open_remote(N03 % pref)
    name = [n for n in zf.namelist() if n.endswith(".geojson")][0]
    out.write_bytes(zf.read(name))
    return out, hf.fetched


def mercator(lon, lat):
    x = lon
    y = math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))
    return x, y


class Frame:
    """経緯度をSVG座標に写す枠。本土と沖縄で別々に持つ。"""

    def __init__(self, box, scale, ox, oy):
        lon0, lat0, lon1, lat1 = box
        # メルカトルのyは**北ほど大きい**が、画面のyは**下ほど大きい**。
        # そのままdy=y-y0を使うと地図が上下反転して画面外へ飛ぶ。yは引き算の向きを逆にする。
        self.x0, self.ytop = mercator(lon0, lat1)     # 左上（北）
        self.x1, self.ybot = mercator(lon1, lat0)     # 右下（南）
        self.scale, self.ox, self.oy = scale, ox, oy
        self.box = box

    def contains(self, lon, lat):
        lon0, lat0, lon1, lat1 = self.box
        return lon0 <= lon <= lon1 and lat0 <= lat <= lat1

    def to_px(self, lon, lat):
        x, y = mercator(lon, lat)
        return (self.ox + (x - self.x0) * self.scale,
                self.oy + (self.ytop - y) * self.scale)

    @property
    def size(self):
        return ((self.x1 - self.x0) * self.scale,
                (self.ytop - self.ybot) * self.scale)


def rings_to_path(rings, frame, keep):
    """多角形の環をSVGのd属性にする。枠の外にある環は落とす。"""
    parts = []
    for ring in rings:
        if len(ring) < 4:
            continue
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        cx, cy = sum(lons) / len(lons), sum(lats) / len(lats)
        if not frame.contains(cx, cy):
            continue
        # 小さすぎる島は描いても見えないので落とす（面積の近似）
        if keep and (max(lons) - min(lons)) * (max(lats) - min(lats)) < keep:
            continue
        pts = [frame.to_px(p[0], p[1]) for p in ring]
        d = "M%.1f %.1f" % pts[0] + "".join("L%.1f %.1f" % p for p in pts[1:]) + "Z"
        parts.append(d)
    return "".join(parts)


def geom_rings(g):
    """GeoJSONのgeometryから外環だけ取り出す（穴は描かない）。"""
    t = g["type"]
    if t == "Polygon":
        return [g["coordinates"][0]]
    if t == "MultiPolygon":
        return [poly[0] for poly in g["coordinates"]]
    return []


def keep_rings(geojson_str, min_area):
    """微小な島を落とし、座標を4桁に丸めて軽くする。

    融合直後の静岡県は1788環・235KBあった。ほとんどは1px未満の岩礁で、
    描いても見えないうえに全県ぶんで10MBを超える。
    """
    if not geojson_str:
        return []
    out = []
    for ring in geom_rings(json.loads(geojson_str)):
        if len(ring) < 4:
            continue
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        if (max(lons) - min(lons)) * (max(lats) - min(lats)) < min_area:
            continue
        if not (in_box(MAIN, lons, lats) or in_box(OKI, lons, lats)):
            continue
        out.append([[round(p[0], 4), round(p[1], 4)] for p in ring])
    return out


def in_box(box, lons, lats):
    lon0, lat0, lon1, lat1 = box
    cx = sum(lons) / len(lons)
    cy = sum(lats) / len(lats)
    return lon0 <= cx <= lon1 and lat0 <= cy <= lat1


def build(con, path, pref, targets):
    """1県ぶん。県輪郭と、対象自治体の形をGeoJSONで返す。

    geojsonは17MBある。自治体ごとに ST_Read を呼ぶと1県35回の再パースになり
    実用にならないので、**1回だけ読んでテーブルに載せてから**問い合わせる。
    """
    p = str(path).replace("\\", "/")
    con.execute("DROP TABLE IF EXISTS n03")
    con.execute("CREATE TEMP TABLE n03 AS "
                "SELECT N03_007 AS code, N03_004 AS nm, geom FROM ST_Read(?)", [p])

    pref_geom = keep_rings(con.execute(
        "SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Union_Agg(geom), ?)) FROM n03",
        [SIMP_PREF]).fetchone()[0], MIN_RING_PREF)

    cities = {}
    SQL = ("SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Union_Agg(geom), ?)) "
           "FROM n03 WHERE %s")
    for code, name in targets:
        # まずコードで引く。政令市はPLATEAUが区を持たないコード（静岡市22100・浜松市22130）を
        # 使うのに対しN03は区で分かれるので、コードでは1件も当たらない。
        # 区コードの規則は市ごとに違う（札幌は01101〜01110の10区、浜松は22138〜22140）ので
        # **市名で集約する**。N03_004は区であっても市名が入っている。
        row = con.execute(SQL % "code = ?", [SIMP_CITY, code]).fetchone()
        r = keep_rings(row[0] if row else None, MIN_RING_CITY)
        if not r:
            row = con.execute(SQL % "nm = ?", [SIMP_CITY, name]).fetchone()
            r = keep_rings(row[0] if row else None, MIN_RING_CITY)
        if r:
            cities[code] = r
        else:
            log("    !! 形が取れず %s %s" % (code, name))
    return pref_geom, cities


if __name__ == "__main__":
    import duckdb

    carte = json.loads((ROOT / "data" / "carte.json").read_text(encoding="utf-8"))
    ok = [c for c in carte["cities"] if c.get("status") == "ok"]
    names = json.loads((ROOT / "data" / "names.json").read_text(encoding="utf-8")) \
        if (ROOT / "data" / "names.json").exists() else {}

    by_pref = {}
    for c in ok:
        by_pref.setdefault(int(c["code"][:2]), []).append((c["code"], names.get(c["code"], c["slug"])))

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    out = {"pref": {}, "city": {}, "grade": {}, "name": {}}
    for c in ok:
        out["grade"][c["code"]] = c["grade"]["year_of_construction"]
        out["name"][c["code"]] = names.get(c["code"], c["slug"])

    t0 = time.time()
    fetched = 0
    for pref in range(1, 48):
        path, f = fetch_geojson(pref)
        fetched += f
        pg, cities = build(con, path, pref, by_pref.get(pref, []))
        out["pref"][pref] = pg
        out["city"].update(cities)
        log("  %02d  県輪郭 %3d環 / 対象 %2d件 / 累計転送 %.0f MB / %.0f 秒"
            % (pref, len(pg), len(cities), fetched / 1e6, time.time() - t0))

    dst = ROOT / "data" / "map_geom.json"
    dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log("")
    log("-> %s (%.1f MB) / 転送 %.0f MB / %.1f 分"
        % (dst.name, dst.stat().st_size / 1e6, fetched / 1e6, (time.time() - t0) / 60))
