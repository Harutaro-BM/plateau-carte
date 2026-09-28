"""PLATEAUの建築年が正しいかを、行政の公表リストで検証する。

静岡市が公表する要安全確認計画記載建築物31棟は、
**すべて昭和56年5月31日以前に建築されたことが行政に確認されている**。
つまり「建築年 ≤ 1981」という真値を持つ建物が31棟、住所付きで手に入る。

カルテは「属性が入っているか」を測っている。これは「**入っている値が正しいか**」を測る。
充填率だけでは分からない層で、公的な正解が存在するのは稀。

同時に沿道閉塞案の両方向検証の片方（再現率）にもなる。
"""
import json
import math
import pathlib
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 同梱の parse_bldg / remote_zip
import parse_bldg as pb  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REFS = ROOT / "refs"
UA = {"User-Agent": "Mozilla/5.0 (plateau-carte/0.1)"}
CUTOFF = 1981          # 昭和56年5月31日以前が旧耐震
RADIUS = 80.0          # 街区までしか解けないので広めに取る


def log(m):
    print(m, flush=True)


def geocode(q):
    u = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(q)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
            d = json.loads(r.read())
        if d:
            c = d[0]["geometry"]["coordinates"]
            return c[0], c[1], d[0]["properties"].get("title")
    except Exception:
        pass
    return None, None, None


def citygml_url():
    u = ("https://www.geospatial.jp/ckan/api/3/action/package_show?id="
         "plateau-22100-shizuoka-shi-2023")
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
        d = json.loads(r.read())["result"]
    gml = [x for x in d["resources"] if "CityGML" in str(x.get("name"))]
    return sorted(gml, key=lambda x: str(x.get("name")))[-1]["url"]


if __name__ == "__main__":
    recs = json.loads((REFS / "shizuoka_31.json").read_text(encoding="utf-8"))
    log("公表建物 %d 棟（すべて昭和56年5月31日以前＝1981年以前が真値）" % len(recs))

    log("")
    log("■ ジオコーディング")
    geo = []
    for r in recs:
        addr = "静岡市" + r["ward"] + r["addr"]
        # 「番地１　外」のような余分を落とす
        addr = addr.split("　")[0].replace(" ", "")
        lon, lat, title = geocode(addr)
        time.sleep(0.35)
        if lon:
            geo.append({**r, "q": addr, "lon": lon, "lat": lat, "hit": title})
        else:
            log("  NG %s" % addr)
    log("  成功 %d / %d" % (len(geo), len(recs)))

    log("")
    log("■ 静岡市のPLATEAUを解析")
    url = citygml_url()
    meshes, _ = pb.list_meshes(url)
    log("  メッシュ %d 件" % len(meshes))
    lat0, lon0 = 34.97, 138.38
    recs_b, fetched, _ = pb.parse_city(url, lat0, lon0, None, log, meshes)
    log("  %d 棟 / 転送 %.0f MB" % (len(recs_b), fetched / 1e6))
    kx = math.cos(math.radians(lat0)) * 111320.0

    bl = []
    for b in recs_b:
        if b.get("cx") is None:
            continue
        y = b.get("year_of_construction")
        y = int(y) if (y or "").isdigit() and 1800 < int(y) < 2100 else None
        h = b.get("measured_height")
        bl.append((lon0 + b["cx"] / kx, lat0 + b["cy"] / 110540.0, y,
                   b.get("structure_type"), h if (h and h > 0) else None, b.get("area")))
    log("  位置つき %d 棟 / 建築年あり %.1f%%"
        % (len(bl), sum(1 for x in bl if x[2]) / len(bl) * 100))

    log("")
    log("■ 突合（街区内の最大床面積の建物を候補とする）")
    log("  公表対象は大規模建築物なので、街区内で最も大きいものを当てる仮定")
    out = []
    for g in geo:
        cand = []
        for lon, lat, y, st, h, area in bl:
            dx = (lon - g["lon"]) * kx
            dy = (lat - g["lat"]) * 110540.0
            d = math.hypot(dx, dy)
            if d <= RADIUS:
                cand.append((area or 0, y, st, h, d))
        if not cand:
            out.append({**g, "matched": False})
            continue
        cand.sort(reverse=True)
        area, y, st, h, d = cand[0]
        out.append({**g, "matched": True, "year": y, "struct": st, "height": h,
                    "area": area, "dist": round(d, 1), "nCand": len(cand)})

    ok = [o for o in out if o.get("matched")]
    wy = [o for o in ok if o.get("year")]
    log("  街区内に建物が見つかった: %d / %d" % (len(ok), len(geo)))
    log("  そのうちPLATEAUに建築年がある: %d (%.0f%%)" % (len(wy), len(wy) / max(1, len(ok)) * 100))

    log("")
    log("=" * 76)
    log("■ 結果：PLATEAUの建築年は「1981年以前」を当てられるか")
    log("")
    log("  %-30s%8s%8s%8s%6s" % ("住所", "建築年", "高さm", "底面m2", "評価"))
    correct = 0
    for o in sorted(wy, key=lambda z: z["year"]):
        mark = "○" if o["year"] <= CUTOFF else "×"
        if o["year"] <= CUTOFF:
            correct += 1
        log("  %-30s%8d%8s%8.0f%6s  %s"
            % (o["q"][:29], o["year"], ("%.1f" % o["height"]) if o["height"] else "-",
               o["area"], o["eval"], mark))
    if wy:
        log("")
        log("  **1981年以前と判定できた: %d / %d (%.0f%%)**" % (correct, len(wy), correct / len(wy) * 100))
        ys = sorted(o["year"] for o in wy)
        log("  建築年の範囲: %d〜%d（中央値 %d）" % (ys[0], ys[-1], ys[len(ys) // 2]))
    hs = [o["height"] for o in ok if o.get("height")]
    if hs:
        hs.sort()
        log("")
        log("  高さ: 最小 %.1f / 中央値 %.1f / 最大 %.1f m" % (hs[0], hs[len(hs) // 2], hs[-1]))

    (DATA / "verify_year.json").write_text(json.dumps({
        "nPublished": len(recs), "nGeocoded": len(geo), "nMatched": len(ok),
        "nWithYear": len(wy), "nCorrect": correct,
        "accuracy": round(correct / len(wy), 4) if wy else None,
        "records": out,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("")
    log("-> data/verify_year.json")
