"""PLATEAUカルテ：全国の自治体について属性の実測充填率を測る。

公式が公表しているのは「その属性が仕様上あるか」の一覧（○×）で、
実測の充填率ではない。実際には同じ○の中に 建築年73.8% と 0% が混在する。
カルテはそこを測る。

## サンプリングの設計

全メッシュを読むと1自治体で数十〜数百MB、306自治体で数十GBになり非現実的。
検証（キルテストa）で、市域を10帯に分けた充填率のばらつきは
1.6〜17.6ptだった。「94% 対 19.8%」の桁の差を判定するには十分だが、
小数点1桁の順位付けには足りない。**だから出力は段階評価にする。**

メッシュの選び方には注意が要る。アルファベット順の先頭3件を取ると
市域の縁を引いて建物0件になる（台東区で実際に起きた）。
**非圧縮サイズの大きいメッシュ＝建物が多い内部のメッシュを選ぶ。**
"""
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 同梱の parse_bldg / remote_zip
import parse_bldg as pb  # noqa: E402
import remote_zip as rz  # noqa: E402

# 全国走査は1メッシュを1度しか読まないのでキャッシュに意味がない。
# 入れたままだと306自治体×24メッシュで100GBを超えてディスクを埋める（実際に起きた）。
pb.USE_CACHE = False

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COVERAGE = (ROOT / "data" / "plateau_coverage.json")
UA = {"User-Agent": "Mozilla/5.0 (plateau-carte/0.1)"}
MESH_PER_CITY = 24    # 層化抽出。層あたり6メッシュ
STRATA = 4            # 総バイト数で4等分した層

# 測る属性と、ダミー値（値として入っているが情報が無いもの）
DUMMY = {
    "usage": {"461"},                 # 461 = 不明
    "storeys": {"9999"},
    "measured_height": set(),         # -9999 は数値判定で除く
    "year_of_construction": set(),
    "structure_type": set(),
    "fireproof": set(),
    "survey_year": {"0001"},
    "lod1_height_type": set(),
}
LABELS = {
    "measured_height": "高さ",
    "usage": "用途",
    "storeys": "階数",
    "year_of_construction": "建築年",
    "structure_type": "構造種別",
    "fireproof": "耐火構造",
    "survey_year": "測量年",
    "lod1_height_type": "高さ種別",
}


def log(m):
    print(m, flush=True)


class Transient(Exception):
    """通信の一時障害。「そのデータセットは無い」とは別物として扱う。"""


def ckan(name, tries=4):
    """CKANに問い合わせる。

    以前はすべての例外を握りつぶして None を返していた。
    その結果、回線が落ちた194自治体が「データセット見つからず」として記録された
    （2026-08-30の全国走査）。**無いのか、聞けなかったのかは区別しないといけない。**
    404 は「無い」、それ以外の失敗は Transient として投げ直す。
    """
    u = "https://www.geospatial.jp/ckan/api/3/action/package_show?id=" + urllib.parse.quote(name)
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
                return json.loads(r.read())["result"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None          # 本当に無い
            last = e
        except Exception as e:
            last = e
        if i < tries - 1:
            time.sleep(2 ** i * 3)   # 3秒・6秒・12秒
    raise Transient("%s: %s" % (type(last).__name__, str(last)[:60]))


def latest_citygml(code, slug, years):
    """最新年度のCityGML zip URLを返す。年度が新しい順に試す。"""
    for y in sorted(years, reverse=True):
        d = ckan("plateau-%s-%s-%d" % (code, slug, y))
        if not d:
            continue
        gml = [r for r in d.get("resources", []) if "CityGML" in str(r.get("name"))]
        # ユースケース個別のCityGML（例「【uc25-16】…のCityGMLデータ」）を拾わないこと。
        # 名前順の最後を取ると「【」で始まるこれらが選ばれ、市域全体ではなく
        # 小さな実証区域だけを読んでしまう（台東区で0件、堺市でn=145になった）。
        main = [r for r in gml if str(r.get("name")).startswith("CityGML")]
        pick = main or gml
        if pick:
            best = sorted(pick, key=lambda r: str(r.get("name")))[-1]
            return best["url"], y, best.get("name")
    return None, None, None


def pick_meshes(url, k=MESH_PER_CITY):
    """非圧縮サイズで層に分け、各層から引く（層化抽出）。

    ## なぜ上位k件ではだめか

    最初は「アルファベット順の先頭を取ると市域の縁を引いて建物0件になる」ので
    **非圧縮サイズの大きい順に3件**を取っていた。0件問題は直ったが、
    別セッションの全数実測（12自治体）で**充填率が系統的に上振れする**と分かった。
    鉾田市は標本 71.7% に対し全数 5.0%（235メッシュ中3つにしか属性が無い）。

    サイズが大きいメッシュは建物が密な市街地で、属性はそこに整備されている。
    **上位k件を取る限り、kを増やしても直らない**（鉾田市で k=24 でも 16.0%、真値5.0%）。

    ## 層化抽出

    サイズ順に並べ、**総バイト数が等しくなるように STRATA 層**へ切り、各層から均等に引く。
    層の切り方を件数ではなくバイト数で等分するのは、
    少数の巨大メッシュが1層に集まって重みが偏るのを避けるため。

    シミュレーション（`sim_sampling.py`）では、鉾田市で誤差 66.7pt → 5.0pt、
    下田市で 19.7pt → 5.6pt。**読み取り棟数は上位3件とほぼ同じ**なので走査コストは増えない。

    返り値の第3要素は層ごとの重み（層の総バイト数）。`measure()` の合成に使う。
    """
    zf, hf = rz.open_remote(url)
    members = rz.members(zf, "/bldg/")
    if not members:
        return [], 0, []
    sized = sorted(members, key=lambda n: zf.getinfo(n).file_size, reverse=True)
    bytes_of = {n: zf.getinfo(n).file_size for n in sized}
    total = sum(bytes_of.values())
    if not total:
        return [], 0, []

    # 総バイト数で等分した層に切る
    strata, cur, acc = [], [], 0.0
    for n in sized:
        cur.append(n)
        acc += bytes_of[n]
        if acc >= total / STRATA * (len(strata) + 1) and len(strata) < STRATA - 1:
            strata.append(cur)
            cur = []
    strata.append(cur)
    strata = [st for st in strata if st]

    # 層の中も**等間隔で決定的に**取る。乱数を使わないので seed に依存せず、
    # 誰が走らせても同じメッシュが選ばれる。再現性を主張する以上ここは譲れない。
    # 5自治体の全メッシュ実測での比較（sim_sampling.py）:
    #   top-3（旧）  平均 21.0pt / 最悪 66.7pt
    #   層化＋乱数     平均  3.1pt / 最悪 22.5pt   （k=8）
    #   層化＋等間隔   平均  0.9pt / 最悪  2.8pt   （k=24）
    per = max(1, k // len(strata))
    picked, weights = [], []
    for st in strata:
        step = max(1, len(st) // per)
        take = st[::step][:per]
        picked.append({pb.mesh_of(n) for n in take})
        weights.append(sum(bytes_of[n] for n in st))
    return picked, hf.fetched, weights


def grade(rate):
    """段階評価。サンプリング誤差に埋もれない粒度にする。"""
    if rate is None:
        return "-"
    if rate >= 0.90:
        return "◎"
    if rate >= 0.60:
        return "○"
    if rate >= 0.20:
        return "△"
    if rate > 0.0:
        return "▲"
    return "×"


def _count(recs, key):
    """その属性に「情報として意味のある値」が入っている棟数。"""
    if key == "measured_height":
        return sum(1 for r in recs if isinstance(r.get(key), float) and r[key] > 0)
    if key == "year_of_construction":
        return sum(1 for r in recs
                   if (r.get(key) or "").isdigit() and 1800 < int(r[key]) < 2100)
    bad = DUMMY.get(key, set())
    return sum(1 for r in recs if r.get(key) and str(r[key]) not in bad)


def measure(per_stratum, weights):
    """層ごとの充填率を、層の総バイト数の比で合成する。

    単純にすべての標本を1つに混ぜると、建物が多い層（＝市街地）の声が大きくなり、
    充填率が上振れする。層の重みで合成することでそれを打ち消す。
    重みにバイト数を使えるのは、1棟あたりバイト数が自治体内で安定しているため
    （鉾田市の235メッシュで8,000〜11,000の範囲に収まる）。
    """
    out = {}
    for key in LABELS:
        num = den = 0.0
        for recs, w in zip(per_stratum, weights):
            if not recs:
                continue
            num += w * (_count(recs, key) / len(recs))
            den += w
        out[key] = round(num / den, 4) if den else None
    return out


def derive(f):
    """充填率から「どの分析ができるか」を導く。カルテの価値はここ。"""
    def ok(k, th):
        return (f.get(k) or 0) >= th
    return {
        # 建物単位人口：用途で住宅を特定し、階数で床面積を作る
        "人口推定": ok("usage", 0.60) and ok("storeys", 0.60),
        # 高さ解析：高さと、その定義（軒高か最高高さか）
        "高さ解析": ok("measured_height", 0.90) and ok("lod1_height_type", 0.60),
        # 耐震・被害推定：建築年で新耐震前後、構造で木造/非木造
        "耐震_被害推定": ok("year_of_construction", 0.40) and ok("structure_type", 0.60),
        # 時系列：測量年が分かるか（年度＝整備年度なので測量年が別途必要）
        "時系列比較": ok("survey_year", 0.60),
    }


def load():
    p = DATA / "carte.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return {c["code"]: c for c in d.get("cities", [])}
    return {}


def save(cities):
    """一時ファイルに書いてから置き換える。

    直接 write_text すると、書き込み中に落ちたとき（ディスク満杯など）
    既存の結果ごと0バイトになる。実際に135自治体ぶんを失った。
    """
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "carte.json.tmp"
    tmp.write_text(
        json.dumps({"generated": time.strftime("%Y-%m-%d"),
                    "note": "各自治体の最新年度CityGMLから、非圧縮サイズで4層に分けた層化抽出"
                            "（層あたり2メッシュ）で実測し、層の総バイト数で重み付けて合成",
                    "meshPerCity": MESH_PER_CITY,
                    "labels": LABELS,
                    "cities": sorted(cities.values(), key=lambda c: c["code"])},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA / "carte.json")


def scan_city(code, slug, years):
    url, year, spec = latest_citygml(code, slug, years)
    if not url:
        return {"code": code, "slug": slug, "status": "データセット見つからず"}
    picked, fetched1, weights = pick_meshes(url)
    if not picked:
        return {"code": code, "slug": slug, "status": "建物GMLなし", "year": year}

    # 層ごとに読む。層の境目を混ぜないこと（混ぜると重み付けの意味が無くなる）
    per_stratum, meshes, fetched2, n = [], [], 0, 0
    for st in picked:
        recs, f2, _ = pb.parse_city(url, 35.0, 135.0, None, None, st)
        per_stratum.append(recs)
        meshes.extend(st)
        fetched2 += f2
        n += len(recs)
    if not n:
        return {"code": code, "slug": slug, "status": "建物0", "year": year}

    f = measure(per_stratum, weights)
    return {
        "code": code, "slug": slug, "year": year, "spec": spec,
        "status": "ok", "nSample": n, "meshes": sorted(meshes),
        "nStrata": len(per_stratum),
        "strataN": [len(r) for r in per_stratum],
        "fill": f,
        "grade": {k: grade(v) for k, v in f.items()},
        "can": derive(f),
        "fetchedMB": round((fetched1 + fetched2) / 1e6, 2),
    }


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    cov = json.loads(COVERAGE.read_text(encoding="utf-8"))
    targets = cov["cities"]
    if limit:
        targets = targets[:limit]
    done = load()
    log("=== PLATEAUカルテ 走査 ===")
    log("対象 %d 自治体 / 済 %d / 1自治体あたり %d メッシュ" % (len(targets), len(done), MESH_PER_CITY))
    t0 = time.time()
    for i, c in enumerate(targets, 1):
        if c["code"] in done and done[c["code"]].get("status") == "ok":
            continue
        try:
            r = scan_city(c["code"], c["slug"], c["years"])
        except Transient as e:
            r = {"code": c["code"], "slug": c["slug"], "status": "通信失敗（要再実行） %s" % e}
        except Exception as e:
            r = {"code": c["code"], "slug": c["slug"],
                 "status": "失敗 %s: %s" % (type(e).__name__, str(e)[:80])}
        done[c["code"]] = r
        if r.get("status") == "ok":
            g = r["grade"]
            log("  %4d/%d %s %s %s年 n=%-5d 高さ%s 用途%s 階数%s 建築年%s 構造%s 耐火%s 測量年%s"
                % (i, len(targets), c["code"], c["slug"][:18], r.get("year"), r["nSample"],
                   g["measured_height"], g["usage"], g["storeys"],
                   g["year_of_construction"], g["structure_type"], g["fireproof"],
                   g["survey_year"]))
        else:
            log("  %4d/%d %s %s -> %s" % (i, len(targets), c["code"], c["slug"][:18], r["status"]))
        if i % 5 == 0:
            save(done)
    save(done)
    el = time.time() - t0
    ok = [c for c in done.values() if c.get("status") == "ok"]
    log("")
    log("完了 %d/%d 件 / 経過 %.1f 分 / 転送 %.1f MB"
        % (len(ok), len(done), el / 60, sum(c.get("fetchedMB", 0) for c in ok)))
    log("-> data/carte.json")
