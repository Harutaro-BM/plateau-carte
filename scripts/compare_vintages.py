"""同じ自治体の最古年度と最新年度で、属性の充填率を比べる。

## 何のためか

カルテは国交省のデータ整備のムラを指摘する性格を持つ。
年度を追って充填率が上がっていれば、**主催者が自分の改善を示せる計器**にもなる。

## 同じ街を比べるための約束

年度によって整備範囲が違う（あとの年度で市域が広がる、など）。
範囲の違いを混ぜると「整備が進んだ」のか「新しい区域を足した」のかが区別できない。

- **両年度に共通するメッシュだけ**を母集団にする
- 層化抽出は scan.py と同じ方法（総バイト数で4等分、層内等間隔）を、共通メッシュに対して行う
- 層の重みは**新しい年度のバイト数**を両年度で共用する。違いがデータの中身だけになるように
- 両年度で**同じメッシュ番号**を読む

建物単位の変化検出は、建物IDが年度間で継承されないため不成立だった（2026-08-19中止）。
ここでは**自治体単位の集計値**だけを比べるので、建物IDは使わない。

使い方:
    python scripts/compare_vintages.py            # 全件（中断しても続きから）
    python scripts/compare_vintages.py 22221 13101 # 指定自治体だけ（試走用）
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scan  # noqa: E402  （pb.USE_CACHE = False もここで効く）
from scan import pb, rz  # noqa: E402

ROOT = scan.ROOT
OUT = ROOT / "data" / "vintage_compare.json"


def sized_meshes(url):
    """メッシュ番号 -> 非圧縮バイト数。建物GMLだけ。"""
    zf, hf = rz.open_remote(url)
    out = {}
    for n in rz.members(zf, "/bldg/"):
        m = pb.mesh_of(n)
        out[m] = out.get(m, 0) + zf.getinfo(n).file_size
    return out, hf.fetched


def stratify(sizes, k=scan.MESH_PER_CITY, strata_n=scan.STRATA):
    """scan.pick_meshes と同じ層化・等間隔抽出を、メッシュ->バイト数の辞書に対して行う。"""
    sized = sorted(sizes, key=lambda m: sizes[m], reverse=True)
    total = sum(sizes.values())
    if not total:
        return [], []
    strata, cur, acc = [], [], 0.0
    for m in sized:
        cur.append(m)
        acc += sizes[m]
        if acc >= total / strata_n * (len(strata) + 1) and len(strata) < strata_n - 1:
            strata.append(cur)
            cur = []
    strata.append(cur)
    strata = [st for st in strata if st]
    per = max(1, k // len(strata))
    picked, weights = [], []
    for st in strata:
        step = max(1, len(st) // per)
        picked.append(set(st[::step][:per]))
        weights.append(sum(sizes[m] for m in st))
    return picked, weights


def read(url, picked):
    per, n, fetched = [], 0, 0
    for st in picked:
        recs, f2, _ = pb.parse_city(url, 35.0, 135.0, None, None, st)
        per.append(recs)
        n += len(recs)
        fetched += f2
    return per, n, fetched


def compare_city(c):
    years = sorted(c["years"])
    y_old, y_new = years[0], years[-1]
    url_new, _, spec_new = scan.latest_citygml(c["code"], c["slug"], [y_new])
    url_old, _, spec_old = scan.latest_citygml(c["code"], c["slug"], [y_old])
    base = {"code": c["code"], "slug": c["slug"], "yearOld": y_old, "yearNew": y_new}
    if not url_new or not url_old:
        return {**base, "status": "データセット見つからず（%s）" % ("旧" if not url_old else "新")}

    s_new, f1 = sized_meshes(url_new)
    s_old, f2 = sized_meshes(url_old)
    common = {m: s_new[m] for m in s_new if m in s_old}
    if not common:
        return {**base, "status": "共通メッシュなし", "nMeshOld": len(s_old), "nMeshNew": len(s_new)}

    picked, weights = stratify(common)
    per_new, n_new, f3 = read(url_new, picked)
    per_old, n_old, f4 = read(url_old, picked)
    if not n_new or not n_old:
        return {**base, "status": "建物0（%s）" % ("旧" if not n_old else "新")}

    fill_new = scan.measure(per_new, weights)
    fill_old = scan.measure(per_old, weights)
    return {
        **base, "status": "ok", "specOld": spec_old, "specNew": spec_new,
        "nMeshOld": len(s_old), "nMeshNew": len(s_new), "nCommon": len(common),
        # 新年度のメッシュのうち旧年度にもあった割合。低いほど「範囲が広がった」自治体
        "commonRatio": round(len(common) / len(s_new), 3),
        "meshes": sorted(set().union(*picked)),
        "nOld": n_old, "nNew": n_new,
        "fillOld": fill_old, "fillNew": fill_new,
        "gradeOld": {k: scan.grade(v) for k, v in fill_old.items()},
        "gradeNew": {k: scan.grade(v) for k, v in fill_new.items()},
        "fetchedMB": round((f1 + f2 + f3 + f4) / 1e6, 2),
    }


def load():
    if OUT.exists():
        return {c["code"]: c for c in json.loads(OUT.read_text(encoding="utf-8"))["cities"]}
    return {}


def save(done):
    # scan.py と同じく一時ファイル経由。書き込み中に落ちても既存結果を壊さない
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%d"),
        "method": "最古年度と最新年度の共通メッシュを層化抽出し、同じメッシュを両年度で読む",
        "cities": sorted(done.values(), key=lambda c: c["code"]),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)


def _run(c):
    """ワーカープロセスで1自治体を処理する。例外は結果として返す（プールを止めない）。"""
    t1 = time.time()
    try:
        r = compare_city(c)
    except scan.Transient as e:
        r = {"code": c["code"], "slug": c["slug"], "status": "通信失敗（要再実行） %s" % e}
    except Exception as e:
        r = {"code": c["code"], "slug": c["slug"],
             "status": "失敗 %s: %s" % (type(e).__name__, str(e)[:80])}
    # 1件ごとの所要秒。遅い自治体の原因（巨大メッシュ・旧仕様）を後で追えるように残す
    r["sec"] = round(time.time() - t1, 1)
    return r


if __name__ == "__main__":
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # 1件あたり数分かかる。遅さの内訳は、1MB単位のレンジ要求の往復（約0.4秒/回）と、
    # 1コアしか使わないXML解析。自治体単位で並行させれば両方が隠れる。
    # 相手は公共のデータ配信なので並列数は控えめにする。
    WORKERS = 4
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cov = json.loads(scan.COVERAGE.read_text(encoding="utf-8"))
    targets = [c for c in cov["cities"] if len(c["years"]) >= 2]
    if args:
        targets = [c for c in targets if c["code"] in set(args)]
    done = load()
    todo = [c for c in targets if done.get(c["code"], {}).get("status") != "ok"]
    scan.log("=== 年度比較 対象 %d 自治体 / 済 %d / 残り %d / 並列 %d ===" % (
        len(targets), len(targets) - len(todo), len(todo), WORKERS))
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_run, c): c for c in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            c, r = futs[fu], fu.result()
            done[c["code"]] = r
            # 1件ごとに保存する。中断しても終わった分は失わない
            save(done)
            if r.get("status") == "ok":
                go, gn = r["gradeOld"], r["gradeNew"]
                moved = " ".join("%s%s→%s" % (scan.LABELS[k][:2], go[k], gn[k])
                                 for k in scan.LABELS if go[k] != gn[k])
                scan.log("  %3d/%d %s %-18s %d→%d 共通%3.0f%% n=%d/%d %4.0f秒 %s" % (
                    i, len(todo), c["code"], c["slug"], r["yearOld"], r["yearNew"],
                    r["commonRatio"] * 100, r["nOld"], r["nNew"], r["sec"], moved or "変化なし"))
            else:
                scan.log("  %3d/%d %s %-18s %s" % (i, len(todo), c["code"], c["slug"], r["status"]))
    ok = sum(1 for c in done.values() if c.get("status") == "ok")
    scan.log("完了 ok %d / %d件 / 経過 %.1f 分" % (ok, len(targets), (time.time() - t0) / 60))
