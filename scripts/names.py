"""自治体コード -> 日本語名 を CKAN のデータセット名から一括で作る。

タイトルは「3D都市モデル（Project PLATEAU）河内長野市（2025年度）」の形。
package_search を1回だけ叩いて全件から抽出する。
"""
import json
import pathlib
import re
import urllib.parse
import urllib.request

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
UA = {"User-Agent": "Mozilla/5.0 (plateau-carte/0.1)"}
PAT = re.compile(r"^plateau-(\d{5})-(.+)-(\d{4})$")
TITLE = re.compile(r"）\s*([^（）]{2,20}?)\s*（\s*(\d{4})年度")

names = {}
for start in range(0, 1200, 200):
    u = ("https://www.geospatial.jp/ckan/api/3/action/package_search?q="
         + urllib.parse.quote("plateau") + "&rows=200&start=%d" % start)
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=120) as r:
        res = json.loads(r.read())["result"]
    if not res["results"]:
        break
    for p in res["results"]:
        m = PAT.match(p["name"] or "")
        if not m:
            continue
        t = TITLE.search(p.get("title") or "")
        if t:
            names.setdefault(m.group(1), t.group(1))
    print("start=%d 累計 %d 件" % (start, len(names)), flush=True)

DATA.mkdir(parents=True, exist_ok=True)
(DATA / "names.json").write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
print("-> data/names.json  %d 件" % len(names))
print("例:", list(names.items())[:6])
