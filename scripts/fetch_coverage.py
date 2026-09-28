"""G空間情報センター(CKAN)から PLATEAU 整備済み自治体の一覧を取得する。

package_search はパッケージ本体（リソース多数）を返すため非常に遅い。
名前だけ必要なので package_list を使う。データセット名の規約は
  plateau-{5桁の団体コード}-{slug}-{年度}
同一自治体が複数年度ぶん存在するので団体コードで一意化する。
"""
import json
import pathlib
import re
import urllib.request

# plateau-height-benchmark から移植（2026-09-28）。出力先だけカルテの data/ に変えた
OUT = pathlib.Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (plateau-carte/0.1)"}
LIST = "https://www.geospatial.jp/ckan/api/3/action/package_list"
PAT = re.compile(r"^plateau-(\d{5})-(.+)-(\d{4})$")

# 自治体コードの形をしているが自治体ではないもの。総務省の市区町村一覧と突き合わせて発見した。
NOT_MUNICIPALITY = {
    "27999": "2025年大阪・関西万博会場（非営利限定ライセンス。自治体ではない）",
}

req = urllib.request.Request(LIST, headers=UA)
with urllib.request.urlopen(req, timeout=100) as r:
    names = json.loads(r.read())["result"]

pl = [n for n in names if n.startswith("plateau-")]
cities, unmatched = {}, []
for n in pl:
    m = PAT.match(n)
    if not m:
        unmatched.append(n)
        continue
    code, slug, year = m.group(1), m.group(2), int(m.group(3))
    if code in NOT_MUNICIPALITY:
        continue
    c = cities.setdefault(code, {"code": code, "slug": slug, "years": set()})
    c["years"].add(year)
    if year >= max(c["years"]):
        c["slug"] = slug

for c in cities.values():
    c["years"] = sorted(c["years"])
    c["latestYear"] = c["years"][-1]

by_pref = {}
for code in cities:
    by_pref[code[:2]] = by_pref.get(code[:2], 0) + 1

out = {
    "source": "G空間情報センター CKAN package_list",
    "excluded": NOT_MUNICIPALITY,
    "note": "データセット名 plateau-{団体コード}-{slug}-{年度} から抽出",
    "plateauDatasets": len(pl),
    "unmatchedNames": unmatched,
    "municipalities": len(cities),
    "prefectures": len(by_pref),
    "byPrefecture": dict(sorted(by_pref.items())),
    "cities": sorted(cities.values(), key=lambda c: c["code"]),
}
(OUT / "plateau_coverage.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"plateau-* datasets : {len(pl)}")
print(f"name pattern 不一致 : {len(unmatched)} {unmatched[:5]}")
print(f"unique 自治体      : {len(cities)}")
print(f"都道府県           : {len(by_pref)} / 47")
print(f"最新年度の分布     : ", end="")
yrs = {}
for c in cities.values():
    yrs[c["latestYear"]] = yrs.get(c["latestYear"], 0) + 1
print(dict(sorted(yrs.items())))
