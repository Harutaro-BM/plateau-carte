"""306自治体のメッシュ数と非圧縮サイズ分布を調べる。

3メッシュ標本がどれだけ市域を代表しているかは、メッシュ数で決まる。
鉾田市は235メッシュ中3つ（棟数で5.2%）しか見ていなかった。

zipの**中央ディレクトリだけ**読めば、建物GMLのメンバ名とサイズが分かる。
GMLの中身は落とさないので1自治体あたり1MB以下で済む。

出力の `sampledShare` は「上位3メッシュの非圧縮サイズ / 全メッシュの合計」。
1棟あたりバイト数は自治体内で安定しているので、**標本が棟数の何割を見ているか**の代理になる。
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 同梱の parse_bldg / remote_zip
import parse_bldg as pb  # noqa: E402
import remote_zip as rz  # noqa: E402
import scan  # noqa: E402

OUT = ROOT / "data" / "mesh_survey.json"


def log(m):
    print(m, flush=True)


def load():
    if OUT.exists():
        return {r["code"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))}
    return {}


def save(d):
    OUT.write_text(json.dumps(sorted(d.values(), key=lambda r: r["code"]),
                              ensure_ascii=False), encoding="utf-8")


def survey(code, slug, years):
    url, year, _ = scan.latest_citygml(code, slug, years)
    if not url:
        return {"code": code, "status": "URLなし"}
    zf, hf = rz.open_remote(url)
    members = rz.members(zf, "/bldg/")
    if not members:
        return {"code": code, "status": "建物GMLなし", "year": year}
    sizes = sorted((zf.getinfo(n).file_size for n in members), reverse=True)
    total = sum(sizes)
    return {
        "code": code, "year": year, "status": "ok",
        "nMesh": len(sizes),
        "totalMB": round(total / 1e6, 1),
        # 上位3メッシュが全体のバイト数に占める割合＝いまの標本が見ている割合の目安
        "sampledShare": round(sum(sizes[:3]) / total, 4) if total else 0,
        "fetchedMB": round(hf.fetched / 1e6, 2),
    }


if __name__ == "__main__":
    cov = json.loads((ROOT / "data" / "plateau_coverage.json").read_text(encoding="utf-8"))
    done = load()
    targets = cov["cities"]
    log("=== メッシュ数の調査 %d自治体 / 済 %d ===" % (len(targets), len(done)))
    t0 = time.time()
    for i, c in enumerate(targets, 1):
        if c["code"] in done and done[c["code"]].get("status") == "ok":
            continue
        try:
            r = survey(c["code"], c["slug"], c["years"])
        except Exception as e:
            r = {"code": c["code"], "status": "失敗 %s" % type(e).__name__}
        done[c["code"]] = r
        if r.get("status") == "ok":
            log("  %4d/%d %s  %4dメッシュ  %7.1fMB  上位3で %5.1f%%"
                % (i, len(targets), c["code"], r["nMesh"], r["totalMB"], r["sampledShare"] * 100))
        else:
            log("  %4d/%d %s  -> %s" % (i, len(targets), c["code"], r["status"]))
        if i % 10 == 0:
            save(done)
    save(done)
    ok = [r for r in done.values() if r.get("status") == "ok"]
    log("")
    log("完了 %d件 / %.1f分 / 転送 %.0fMB"
        % (len(ok), (time.time() - t0) / 60, sum(r.get("fetchedMB", 0) for r in ok)))
