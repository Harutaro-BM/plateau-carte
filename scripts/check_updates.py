"""PLATEAU の更新を検知する（数値は変えない）。

走査の時点（data/carte.json の generated）と、いまの G空間情報センター（CKAN）を比べて、
走査し直すべき自治体を洗い出す。

- 新しい自治体：一覧に増えた自治体
- 新しい年度：走査した年度より新しい年度のデータセットが出た自治体
- 差し替え：走査した年度のデータセットが、走査の後に更新された自治体（CKAN の更新日時で判定）
- 削除：一覧から消えた自治体（走査し直しはせず、知らせるだけ）

使い方:
    python scripts/check_updates.py          # 結果を表示する
    python scripts/check_updates.py --write  # data/updates.json にも書く
"""
import json
import os
import pathlib
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scan  # noqa: E402

ROOT = scan.ROOT
LIST = "https://www.geospatial.jp/ckan/api/3/action/package_list"
PAT = re.compile(r"^plateau-(\d{5})-(.+)-(\d{4})$")
NOT_MUNICIPALITY = {"27999"}      # fetch_coverage.py と同じ（大阪・関西万博会場）


def current_catalog():
    """いまの CKAN にある PLATEAU 自治体と年度。"""
    with urllib.request.urlopen(urllib.request.Request(LIST, headers=scan.UA), timeout=100) as r:
        names = json.loads(r.read())["result"]
    cities = {}
    for n in names:
        m = PAT.match(n)
        if not m or m.group(1) in NOT_MUNICIPALITY:
            continue
        code, slug, year = m.group(1), m.group(2), int(m.group(3))
        c = cities.setdefault(code, {"code": code, "slug": slug, "years": set()})
        c["years"].add(year)
        if year >= max(c["years"]):
            c["slug"] = slug
    for c in cities.values():
        c["years"] = sorted(c["years"])
    return cities


def modified_after(row, since):
    """走査した年度のデータセット（と、使った CityGML のリソース）が since より後に更新されたか。"""
    d = scan.ckan("plateau-%s-%s-%d" % (row["code"], row["slug"], row["year"]))
    if not d:
        return None
    gml = [r for r in d.get("resources", []) if "CityGML" in str(r.get("name"))]
    main = [r for r in gml if str(r.get("name")).startswith("CityGML")] or gml
    res = sorted(main, key=lambda r: str(r.get("name")))[-1] if main else {}
    stamps = [s for s in (res.get("last_modified"), res.get("metadata_modified"), d.get("metadata_modified")) if s]
    latest = max(stamps) if stamps else ""
    return latest if latest[:10] > since else ""


def check():
    carte = json.loads((ROOT / "data" / "carte.json").read_text(encoding="utf-8"))
    since = carte.get("generated", "")
    scanned = {c["code"]: c for c in carte["cities"] if c.get("status") == "ok"}
    snap = {c["code"]: c for c in json.loads(scan.COVERAGE.read_text(encoding="utf-8"))["cities"]}
    cur = current_catalog()

    new_city = sorted(set(cur) - set(snap))
    removed = sorted(set(snap) - set(cur))
    new_year = [{"code": k, "scanned": scanned[k]["year"], "latest": cur[k]["years"][-1]}
                for k in sorted(set(cur) & set(scanned)) if cur[k]["years"][-1] > scanned[k]["year"]]
    same = [scanned[k] for k in sorted(set(cur) & set(scanned)) if cur[k]["years"][-1] == scanned[k]["year"]]
    with ThreadPoolExecutor(8) as ex:
        stamps = list(ex.map(lambda r: modified_after(r, since), same))
    modified = [{"code": r["code"], "year": r["year"], "modified": s} for r, s in zip(same, stamps) if s]

    return {
        "checkedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "baseline": since,
        "nCatalog": len(cur), "nSnapshot": len(snap),
        "newCities": [{"code": k, "slug": cur[k]["slug"], "years": cur[k]["years"]} for k in new_city],
        "newYears": new_year,
        "modified": modified,
        "removed": removed,
        "catalog": {k: {"slug": v["slug"], "years": v["years"]} for k, v in cur.items()},
    }


def summary(u):
    lines = ["## PLATEAU の更新の検知（走査 %s 以降、%s 時点）" % (u["baseline"], u["checkedAt"]), "",
             "| 種類 | 件数 |", "|---|---:|",
             "| 新しい自治体 | %d |" % len(u["newCities"]),
             "| 新しい年度 | %d |" % len(u["newYears"]),
             "| 走査後に差し替え | %d |" % len(u["modified"]),
             "| 一覧から消えた | %d |" % len(u["removed"]), ""]
    for label, rows, fmt in [
        ("新しい自治体", u["newCities"], lambda r: "%s %s %s" % (r["code"], r["slug"], r["years"])),
        ("新しい年度", u["newYears"], lambda r: "%s：%d → %d年度" % (r["code"], r["scanned"], r["latest"])),
        ("走査後に差し替え", u["modified"], lambda r: "%s %d年度（更新 %s）" % (r["code"], r["year"], r["modified"][:10])),
        ("一覧から消えた", [{"code": c} for c in u["removed"]], lambda r: r["code"]),
    ]:
        if rows:
            lines += ["**%s**：" % label + "、".join(fmt(r) for r in rows[:40]) + ("…" if len(rows) > 40 else ""), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    u = check()
    text = summary(u)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if "--write" in sys.argv:
        (ROOT / "data" / "updates.json").write_text(json.dumps(u, ensure_ascii=False, indent=1), encoding="utf-8")
        print("-> data/updates.json")
