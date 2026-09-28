# plateau-timeseries/scripts から取り込み（2026-09-28）。カルテ単体で動くように同梱している
"""PLATEAU CityGML の建物モデルから比較に必要な最小限を取り出す。

年度によって仕様バージョン(v2〜v5)が違い名前空間も変わるので、
名前空間を無視してローカル名だけで拾う。

高さは2種類を別に持つ:
  measured_height : bldg:measuredHeight（属性としての高さ）
  solid_height    : lod1Solid の zmax-zmin（形状としての高さ）
高さ検証プロジェクトで両者が一致しないことが分かっているため、
年度間比較でもどちらを見ているかを混ぜない。
"""
import hashlib
import io
import math
import pathlib
import xml.etree.ElementTree as ET

import remote_zip as rz

CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache"


# 同じ都市を何度も解析するとき用のキャッシュ。既定で有効。
# **全国走査のように1メッシュを1度しか読まない用途では必ず切ること。**
# 306自治体×24メッシュで104GBに達してディスクを埋めた（2026-08-30）。
USE_CACHE = True


def _cached(zf, name, url):
    """GMLメンバをディスクにキャッシュする。再解析で再ダウンロードしないため。"""
    if not USE_CACHE:
        return zf.read(name)
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((url + "|" + name).encode()).hexdigest()[:20]
    f = CACHE / (key + ".gml")
    if f.exists():
        return f.read_bytes()
    raw = zf.read(name)
    f.write_bytes(raw)
    return raw

Z_EPS = 0.05  # 底面・屋根面を判定する高さの許容差(m)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _first_text(el, name):
    for d in el.iter():
        if _local(d.tag) == name and d.text:
            return d.text.strip()
    return None


def _rings(el):
    """(lat, lon, z) の並びを LinearRing 単位で返す。"""
    out = []
    for d in el.iter():
        if _local(d.tag) != "posList" or not d.text:
            continue
        v = d.text.split()
        if len(v) < 9:
            continue
        try:
            f = [float(x) for x in v]
        except ValueError:
            continue
        if len(f) % 3:
            continue
        out.append([(f[i], f[i + 1], f[i + 2]) for i in range(0, len(f), 3)])
    return out


def _tri_area_centroid(ring, lat0, lon0):
    """緯度経度リングを局所平面に落として面積と重心を返す。"""
    kx = math.cos(math.radians(lat0)) * 111320.0
    ky = 110540.0
    p = [((lon - lon0) * kx, (lat - lat0) * ky) for lat, lon, _ in ring]
    a = cx = cy = 0.0
    for i in range(len(p) - 1):
        cr = p[i][0] * p[i + 1][1] - p[i + 1][0] * p[i][1]
        a += cr
        cx += (p[i][0] + p[i + 1][0]) * cr
        cy += (p[i][1] + p[i + 1][1]) * cr
    if abs(a) < 1e-12:
        return 0.0, 0.0, 0.0
    return abs(a) / 2.0, cx / (3 * a), cy / (3 * a)


def parse_member(raw, lat0, lon0):
    """1つのGMLから建物レコードのリストを返す。"""
    recs = []
    for _, el in ET.iterparse(io.BytesIO(raw), events=("end",)):
        if _local(el.tag) != "Building":
            continue
        gid = None
        for k, v in el.attrib.items():
            if _local(k) == "id":
                gid = v
        mh = _first_text(el, "measuredHeight")
        rec = {
            "gml_id": gid,
            "building_id": _first_text(el, "buildingID"),
            "measured_height": float(mh) if mh else None,
            "survey_year": _first_text(el, "surveyYear"),
            "lod1_height_type": _first_text(el, "lod1HeightType"),
            "src_scale_lod1": _first_text(el, "srcScaleLod1"),
            "usage": _first_text(el, "usage"),
            "storeys": _first_text(el, "storeysAboveGround"),
            # 2025年AWARDファイナリストが静岡で使っていた属性。
            # 自治体によって入っている/いないが分かれるので個別に確認する。
            "year_of_construction": _first_text(el, "yearOfConstruction"),
            "structure_type": _first_text(el, "buildingStructureType"),
            "fireproof": _first_text(el, "fireproofStructureType"),
            "detailed_usage": _first_text(el, "detailedUsage"),
        }
        rings = _rings(el)
        if rings:
            zs = [z for r in rings for _, _, z in r]
            zmin, zmax = min(zs), max(zs)
            rec["zmin"] = zmin
            rec["zmax"] = zmax
            rec["solid_height"] = zmax - zmin
            # 底面（全頂点が zmin）の三角形だけで面積と重心を出す
            area = wx = wy = 0.0
            for r in rings:
                if all(abs(z - zmin) < Z_EPS for _, _, z in r):
                    a, cx, cy = _tri_area_centroid(r, lat0, lon0)
                    area += a
                    wx += a * cx
                    wy += a * cy
            if area > 0:
                rec["area"] = area
                rec["cx"] = wx / area
                rec["cy"] = wy / area
        recs.append(rec)
        el.clear()
    return recs


def mesh_of(name):
    """udx/bldg/50352406_bldg_6697_op.gml -> 50352406"""
    return name.rsplit("/", 1)[-1].split("_", 1)[0]


def list_meshes(url):
    """年度ごとの整備メッシュ集合を返す（整備範囲の比較用）。"""
    zf, hf = rz.open_remote(url)
    return {mesh_of(n) for n in rz.members(zf, "/bldg/")}, hf.fetched


def parse_city(url, lat0, lon0, limit=None, log=None, meshes=None):
    """zipから建物GMLを順に読み、全建物のレコードとHTTP転送量を返す。

    meshes を渡すとそのメッシュだけ読む（年度間で整備範囲が違うため、
    共通メッシュに限定しないと「出現・消滅」を範囲差と混同する）。
    """
    zf, hf = rz.open_remote(url)
    names = sorted(rz.members(zf, "/bldg/"))
    if meshes is not None:
        names = [n for n in names if mesh_of(n) in meshes]
    if limit:
        names = names[:limit]
    recs = []
    for i, n in enumerate(names):
        recs.extend(parse_member(_cached(zf, n, url), lat0, lon0))
        if log and (i + 1) % 20 == 0:
            log(f"  {i+1}/{len(names)} members, {len(recs)} buildings, "
                f"{hf.fetched/1e6:.0f} MB fetched")
    return recs, hf.fetched, len(names)
