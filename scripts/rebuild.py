"""carte.json から下流の生成物を一括で作り直す。

走査をやり直すたびに手で順番を思い出すのは事故のもと。
build_map -> render_map -> web へ配布 -> デッキ照合 の順で固定する。
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable


def run(script):
    print("--- %s ---" % script)
    r = subprocess.run([PY, str(ROOT / "scripts" / script)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").strip()[-1500:])
    if r.returncode:
        print((r.stderr or "").strip()[-1500:])
        raise SystemExit("%s が失敗 (rc=%d)" % (script, r.returncode))


def main():
    d = json.loads((ROOT / "data" / "carte.json").read_text(encoding="utf-8"))
    ok = [c for c in d["cities"] if c.get("status") == "ok"]
    print("carte.json: %d件中 ok %d件" % (len(d["cities"]), len(ok)))
    if len(ok) < 300:
        print("警告: ok が %d件しかない。走査が未完了の可能性" % len(ok))

    run("build_map.py")
    run("render_map.py")

    # ビューアは web/ 配下しか読まないので配布する（ここを忘れて旧データを表示していた）
    for f in ("carte.json", "names.json"):
        shutil.copy(ROOT / "data" / f, ROOT / "web" / f)
        print("web/%s を更新" % f)

    # 過去年度との比較。ビューアの詳細パネルが読むので、使う項目だけに絞って配る（元は約180KB）
    vc = ROOT / "data" / "vintage_compare.json"
    if vc.exists():
        keep = ("status", "yearOld", "yearNew", "commonRatio", "gradeOld", "gradeNew")
        slim = {c["code"]: {k: c[k] for k in keep if k in c}
                for c in json.loads(vc.read_text(encoding="utf-8"))["cities"]}
        (ROOT / "web" / "vintage.json").write_text(
            json.dumps(slim, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("web/vintage.json を更新（%d件）" % len(slim))

    # デッキは自前の地図コピーを参照する。render_map は web/ にしか書かないので
    # ここで配布しないと、PDFだけ旧データの地図のまま出来上がる（実際にやった）
    # 応募デッキは公開リポジトリに含めないので、手元に award/deck がある場合だけ配る
    if (ROOT / "award" / "deck").is_dir():
        for f in ("map.svg", "map-kanto.svg"):
            shutil.copy(ROOT / "web" / f, ROOT / "award" / "deck" / f)
            print("award/deck/%s を更新" % f)

    run("diff_grades.py")
    if (ROOT / "award" / "deck" / "index.html").exists():
        run("check_deck.py")
    print("\n完了")


if __name__ == "__main__":
    main()
