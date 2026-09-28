"""旧（上位3メッシュ）と新（層化抽出）で段階がどれだけ変わったかを出す。

標本の取り方を変えた以上、段階が動くのは当然だが、
「どれだけ動いたか」を示せないと差し替えの根拠にならない。
"""
import json
import pathlib
import collections

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

ORDER = ["×", "▲", "△", "○", "◎"]
KEYS = ["measured_height", "usage", "storeys", "year_of_construction",
        "structure_type", "fireproof", "survey_year", "lod1_height_type"]
LABEL = {"measured_height": "計測高", "usage": "用途", "storeys": "階数",
         "year_of_construction": "建築年", "structure_type": "構造",
         "fireproof": "耐火", "survey_year": "測量年", "lod1_height_type": "LOD1高"}


def load(p):
    d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    return {c["code"]: c for c in d["cities"] if c.get("status") == "ok"}


def main():
    old = load(DATA / "carte_top3_2026-08-19.json")
    new = load(DATA / "carte.json")
    both = sorted(set(old) & set(new))
    print("旧 %d件 / 新 %d件 / 共通 %d件" % (len(old), len(new), len(both)))

    moved = collections.Counter()
    shifts = collections.Counter()
    worst = []
    for code in both:
        for k in KEYS:
            a = old[code]["grade"].get(k)
            b = new[code]["grade"].get(k)
            if a is None or b is None:
                raise SystemExit("段階が取れない: %s %s (旧=%r 新=%r)。"
                                 "キー名の取り違えを黙って飛ばすと"
                                 "『変化なし』に見えてしまう" % (code, k, a, b))
            d = ORDER.index(b) - ORDER.index(a)
            shifts[d] += 1
            if d:
                moved[k] += 1
            if abs(d) >= 2:
                worst.append((abs(d), code, new[code]["slug"], LABEL[k], a, b))

    tot = sum(shifts.values())
    print("\n== 段階の移動 (新 - 旧) ==")
    for d in sorted(shifts):
        n = shifts[d]
        s = "変化なし" if d == 0 else ("%+d段階" % d)
        print("  %-8s %5d件 (%4.1f%%)" % (s, n, n / tot * 100))
    print("  変化ありは %d/%d (%.1f%%)" % (tot - shifts[0], tot, (tot - shifts[0]) / tot * 100))

    print("\n== 項目別の変化件数 ==")
    for k in KEYS:
        print("  %-5s %3d/%d件" % (LABEL[k], moved[k], len(both)))

    print("\n== 2段階以上動いた例（上位15） ==")
    for d, code, slug, lab, a, b in sorted(worst, reverse=True)[:15]:
        print("  %s %-22s %-4s %s -> %s (%+d)" % (code, slug, lab, a, b, ORDER.index(b) - ORDER.index(a)))

    (DATA / "grade_diff.json").write_text(json.dumps({
        "nOld": len(old), "nNew": len(new), "nBoth": len(both),
        "shifts": {str(k): v for k, v in sorted(shifts.items())},
        "movedByKey": dict(moved),
        "changedRatio": round((tot - shifts[0]) / tot, 4),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\ndata/grade_diff.json に保存")


if __name__ == "__main__":
    main()
