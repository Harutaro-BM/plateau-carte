"""年度比較（data/vintage_compare.json）を集計する。

問い：整備は年度を追って良くなっているか。属性ごとに、段階が上がった／変わらない／下がった自治体を数える。

読むときの注意
- 比べているのは両年度に共通するメッシュだけ（compare_vintages.py の約束）。
  共通が少ない自治体（commonRatio が低い）は、市域のごく一部の比較になる
- 古い年度が旧仕様（v1〜v2）のとき、属性の有無が「整備の改善」ではなく「仕様の変化」で動いている可能性がある。
  仕様の組み合わせ別にも数える
"""
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
V = json.loads((ROOT / "data" / "vintage_compare.json").read_text(encoding="utf-8"))
ORDER = ["×", "▲", "△", "○", "◎"]
KEYS = ["measured_height", "usage", "storeys", "year_of_construction",
        "structure_type", "fireproof", "survey_year", "lod1_height_type"]
LABEL = {"measured_height": "高さ", "usage": "用途", "storeys": "階数", "year_of_construction": "建築年",
         "structure_type": "構造種別", "fireproof": "耐火構造", "survey_year": "測量年",
         "lod1_height_type": "高さ種別"}


def spec_ver(s):
    s = s or ""
    for v in ("v1", "v2", "v3", "v4", "v5"):
        if v in s:
            return v
    return "?"


def main():
    allc = V["cities"]
    ok = [c for c in allc if c["status"] == "ok"]
    fail = collections.Counter("7z（部分読み不可）" if "BadZip" in c["status"] else c["status"] for c in allc if c["status"] != "ok")
    print("比較できた %d / %d 自治体。できなかった内訳: %s" % (len(ok), len(allc), dict(fail)))
    low = [c for c in ok if c["commonRatio"] < 0.5]
    print("両年度に共通するメッシュが新年度の半分未満: %d件（市域の一部の比較）" % len(low))

    out = {"nCompared": len(ok), "nTotal": len(allc), "notCompared": dict(fail), "byKey": {}}
    print("\n== 段階の変化（自治体数） ==")
    print("  %-6s %4s %4s %4s   %s" % ("属性", "上がる", "同じ", "下がる", "充填率の変化（中央値・pt）"))
    for k in KEYS:
        up = same = down = 0
        deltas = []
        for c in ok:
            a, b = ORDER.index(c["gradeOld"][k]), ORDER.index(c["gradeNew"][k])
            up += b > a; same += b == a; down += b < a
            deltas.append((c["fillNew"][k] - c["fillOld"][k]) * 100)
        deltas.sort()
        med = deltas[len(deltas) // 2]
        print("  %-6s %4d %4d %4d   %+.1f" % (LABEL[k], up, same, down, med))
        out["byKey"][k] = {"up": up, "same": same, "down": down, "medianDeltaPt": round(med, 1)}

    # 1自治体として、何か1つでも上がった／下がった
    anyup = sum(any(ORDER.index(c["gradeNew"][k]) > ORDER.index(c["gradeOld"][k]) for k in KEYS) for c in ok)
    anydn = sum(any(ORDER.index(c["gradeNew"][k]) < ORDER.index(c["gradeOld"][k]) for k in KEYS) for c in ok)
    both = sum(any(ORDER.index(c["gradeNew"][k]) > ORDER.index(c["gradeOld"][k]) for k in KEYS)
               and any(ORDER.index(c["gradeNew"][k]) < ORDER.index(c["gradeOld"][k]) for k in KEYS) for c in ok)
    none = sum(all(c["gradeNew"][k] == c["gradeOld"][k] for k in KEYS) for c in ok)
    print("\n1つ以上の属性で段階が上がった %d件 ／ 下がった %d件 ／ 両方 %d件 ／ 全属性で変化なし %d件" % (anyup, anydn, both, none))
    out.update({"anyUp": anyup, "anyDown": anydn, "both": both, "noChange": none})

    # 仕様の組み合わせ別：旧仕様から新仕様への切り替えで属性が「生えた」だけではないか
    print("\n== 仕様の組み合わせ別（件数と、1つ以上上がった件数） ==")
    by = collections.defaultdict(list)
    for c in ok:
        by["%s→%s" % (spec_ver(c["specOld"]), spec_ver(c["specNew"]))].append(c)
    for key, cs in sorted(by.items(), key=lambda x: -len(x[1])):
        u = sum(any(ORDER.index(c["gradeNew"][k]) > ORDER.index(c["gradeOld"][k]) for k in KEYS) for c in cs)
        d = sum(any(ORDER.index(c["gradeNew"][k]) < ORDER.index(c["gradeOld"][k]) for k in KEYS) for c in cs)
        print("  %-7s %3d件  上がる %3d  下がる %3d" % (key, len(cs), u, d))

    # 旧年度で0%、新年度で90%以上：整備か、仕様（要素が無かった）か
    print("\n== 旧0% → 新90%以上（×→◎）の件数 ==")
    for k in KEYS:
        n = [c for c in ok if c["fillOld"][k] == 0 and c["fillNew"][k] >= 0.9]
        if n:
            vs = collections.Counter(spec_ver(c["specOld"]) for c in n)
            print("  %-6s %3d件  旧年度の仕様 %s" % (LABEL[k], len(n), dict(vs)))

    # 大きく下がった例（整備の後退か、範囲の違いか）
    print("\n== 充填率が20pt以上下がった例 ==")
    drops = []
    for c in ok:
        for k in KEYS:
            d = (c["fillNew"][k] - c["fillOld"][k]) * 100
            if d <= -20:
                drops.append((d, c["code"], c["slug"], c["yearOld"], c["yearNew"], LABEL[k],
                              c["fillOld"][k] * 100, c["fillNew"][k] * 100, c["commonRatio"]))
    for d in sorted(drops)[:15]:
        print("  %s %-18s %d→%d %-5s %5.1f%% → %5.1f%%（%+.1f）共通%3.0f%%" % (d[1], d[2], d[3], d[4], d[5], d[6], d[7], d[0], d[8] * 100))
    print("  （計 %d件・%d自治体）" % (len(drops), len({d[1] for d in drops})))
    out["drops20"] = len(drops)

    # 東京都：建築年は年度を追っても0のままか
    tk = [c for c in ok if c["code"].startswith("13")]
    y0 = sum(c["fillOld"]["year_of_construction"] == 0 and c["fillNew"]["year_of_construction"] == 0 for c in tk)
    print("\n東京都 %d件のうち、建築年が両年度とも0%%: %d件" % (len(tk), y0))
    out["tokyo"] = {"n": len(tk), "yearZeroBoth": y0}

    (ROOT / "data" / "vintage_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n-> data/vintage_summary.json")


if __name__ == "__main__":
    main()
