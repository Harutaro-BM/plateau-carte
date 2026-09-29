"""PLATEAU の更新に追従する（自動の再走査）。GitHub Actions から月1回呼ばれる。

1. check_updates.py で、走査し直すべき自治体を検知する（常に動く。数値は変えない）
2. config/auto_update.json のフラグが開いているときだけ、該当する自治体を走査し直し、
   地図とビューア用のデータを作り直す

フラグは二重。enabled が true で、かつ今日が notBefore 以降のときだけ走査する。
審査が終わるまでは、応募資料と公開ページの数値を一致させておくために止めておく。

走査し直すと、README やビューアの説明文に書いた数値（306自治体・10,714,532棟 など）とずれる。
説明文は自動では直さないので、再走査の後に人が読み直すこと。
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_updates as cu  # noqa: E402
import scan  # noqa: E402

ROOT = scan.ROOT
CFG = ROOT / "config" / "auto_update.json"


def output(key, value):
    """GitHub Actions の後続の手順に結果を渡す（手元で動かすときは何もしない）。"""
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (key, value))


def gate():
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    today = datetime.date.today().isoformat()
    if not cfg.get("enabled"):
        return False, "停止中（enabled=false）。%s" % cfg.get("reason", "")
    if today < cfg.get("notBefore", "9999-12-31"):
        return False, "停止中（enabled=true だが、%s より前なので走査しない）" % cfg["notBefore"]
    return True, "有効"


def main():
    u = cu.check()
    text = cu.summary(u)
    ok, why = gate()
    text += "\n\n**自動の再走査：%s**\n" % why
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")

    targets = [r["code"] for r in u["newCities"]] + [r["code"] for r in u["newYears"]] + [r["code"] for r in u["modified"]]
    if not ok or not targets:
        output("applied", "false")
        return

    # 走査対象の一覧（スナップショット）を、変わった自治体だけ今の CKAN に合わせる
    cov = json.loads(scan.COVERAGE.read_text(encoding="utf-8"))
    byc = {c["code"]: c for c in cov["cities"]}
    for code in targets:
        cur = u["catalog"][code]
        byc[code] = {**byc.get(code, {}), "code": code, "slug": cur["slug"], "years": cur["years"],
                     "latestYear": cur["years"][-1]}
    cov["cities"] = sorted(byc.values(), key=lambda c: c["code"])
    cov["municipalities"] = len(cov["cities"])
    scan.COVERAGE.write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")

    # 変わった自治体だけ走査し直す（scan.py と同じ関数・同じ標本設計）
    done = scan.load()
    for code in targets:
        c = byc[code]
        try:
            r = scan.scan_city(code, c["slug"], c["years"])
        except Exception as e:
            r = {"code": code, "slug": c["slug"], "status": "失敗 %s: %s" % (type(e).__name__, str(e)[:80])}
        done[code] = r
        print("  再走査 %s %s %s" % (code, c["slug"], r.get("status")))
    scan.save(done)

    if u["newCities"]:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "names.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "rebuild.py")], check=True)

    u["applied"] = targets
    (ROOT / "data" / "updates.json").write_text(json.dumps(u, ensure_ascii=False, indent=1), encoding="utf-8")
    output("applied", "true")
    output("count", str(len(targets)))


if __name__ == "__main__":
    main()
