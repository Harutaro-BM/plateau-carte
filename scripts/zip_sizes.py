"""各自治体の最新年度 CityGML zip の大きさと、そのうち建物（bldg）が占める量を測る。

「開くまで中身が分からない」の中身を数字にする。zip は自治体の全データ（地形・道路・土地利用…）を1つに束ねていて、
建物の属性を確かめるには、建物以外もまとめて落とすことになる。目次（中央ディレクトリ）だけを読むので1件数秒。
"""
import json
import pathlib
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scan  # noqa: E402
from scan import rz  # noqa: E402

ROOT = scan.ROOT


def one(c):
    try:
        url, y, spec = scan.latest_citygml(c["code"], c["slug"], c["years"])
        if not url:
            return {"code": c["code"], "status": "データセット無し"}
        with urllib.request.urlopen(urllib.request.Request(url, headers=scan.UA, method="HEAD"), timeout=60) as r:
            size = int(r.headers["Content-Length"])
        zf, _ = rz.open_remote(url)
        kinds = {}
        for info in zf.infolist():
            parts = info.filename.split("/")
            k = parts[1] if len(parts) >= 3 else parts[0]
            kinds[k] = kinds.get(k, 0) + info.compress_size
        return {"code": c["code"], "slug": c["slug"], "year": y, "status": "ok", "zipBytes": size,
                "bldgBytes": kinds.get("bldg", 0), "kinds": kinds}
    except Exception as e:
        return {"code": c["code"], "status": "失敗 %s" % type(e).__name__}


if __name__ == "__main__":
    cov = json.loads(scan.COVERAGE.read_text(encoding="utf-8"))["cities"]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, cov))
    ok = [r for r in res if r["status"] == "ok"]
    (ROOT / "data" / "zip_sizes.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    zs = sorted(r["zipBytes"] for r in ok)
    share = sorted(r["bldgBytes"] / r["zipBytes"] for r in ok if r["zipBytes"])
    q = lambda a, p: a[min(len(a) - 1, int(len(a) * p))]
    print("成功 %d / %d" % (len(ok), len(res)))
    print("zip の大きさ: 最小 %.2f GB / 中央値 %.2f GB / 90%%点 %.2f GB / 最大 %.2f GB / 合計 %.0f GB" % (
        zs[0] / 1e9, q(zs, .5) / 1e9, q(zs, .9) / 1e9, zs[-1] / 1e9, sum(zs) / 1e9))
    print("うち建物の割合: 中央値 %.1f%% / 最小 %.2f%% / 最大 %.1f%%" % (q(share, .5) * 100, share[0] * 100, share[-1] * 100))
    tot = {}
    for r in ok:
        for k, v in r["kinds"].items():
            tot[k] = tot.get(k, 0) + v
    print("全306自治体の合計の内訳:", ", ".join("%s %.0fGB" % (k, v / 1e9) for k, v in sorted(tot.items(), key=lambda x: -x[1])[:6]))
    print("1.1GB 以下の zip: %d件 / 5GB 以上: %d件" % (sum(z <= 1.1e9 for z in zs), sum(z >= 5e9 for z in zs)))
