# plateau-timeseries/scripts から取り込み（2026-09-28）。カルテ単体で動くように同梱している
"""リモートzipから必要なメンバだけHTTPレンジ要求で取り出す。

PLATEAUのCityGML zipは1自治体で200MB〜1GBあり、大半はDEMと浸水想定区域。
建物モデル(udx/bldg/*.gml)だけ必要なので全体を落とさない。

zipfile.ZipFile は seek/read/tell を持つファイルライクを受け付けるので、
その3つをRangeリクエストで実装すれば標準ライブラリだけで済む。
"""
import io
import time
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0 (plateau-carte/0.1)"}
# 全国走査のような長時間の連続取得では回線が一時的に落ちる。
# リトライが無いと1自治体まるごと失敗として記録されるので入れる（2026-08-31）。
RETRIES = 5
# 中央ディレクトリ読み取りのように細かいseekが連続するので、まとめて取る。
BLOCK = 1 << 20


def _retry(fn, tries=RETRIES):
    """一時的な通信障害を吸収する。3秒・6秒・12秒・24秒と待つ。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(2 ** i * 3)
    raise last


class HttpFile(io.RawIOBase):
    def __init__(self, url):
        self.url = url
        self.pos = 0
        self._cache = {}
        rq = urllib.request.Request(url, headers=UA, method="HEAD")
        r = _retry(lambda: urllib.request.urlopen(rq, timeout=60))
        with r:
            self.size = int(r.headers["Content-Length"])
            if r.headers.get("Accept-Ranges") != "bytes":
                raise RuntimeError("サーバがRangeに対応していない: " + url)
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        if whence == 0:
            self.pos = off
        elif whence == 1:
            self.pos += off
        else:
            self.pos = self.size + off
        return self.pos

    def _block(self, idx):
        if idx not in self._cache:
            lo = idx * BLOCK
            hi = min(lo + BLOCK, self.size) - 1
            rq = urllib.request.Request(self.url, headers={**UA, "Range": f"bytes={lo}-{hi}"})
            self._cache[idx] = _retry(
                lambda: urllib.request.urlopen(rq, timeout=180).read())
            self.fetched += hi - lo + 1
            # 巨大zipで中央ディレクトリが遠い場合にメモリを食いすぎないよう上限を設ける
            if len(self._cache) > 400:
                for k in sorted(self._cache)[:200]:
                    if k != idx:
                        del self._cache[k]
        return self._cache[idx]

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        got = 0
        while got < n:
            idx = (self.pos + got) // BLOCK
            blk = self._block(idx)
            start = (self.pos + got) - idx * BLOCK
            take = min(n - got, len(blk) - start)
            b[got:got + take] = blk[start:start + take]
            got += take
        self.pos += got
        return got


def open_remote(url):
    """(ZipFile, HttpFile) を返す。HttpFile.fetched で実転送量が分かる。"""
    hf = HttpFile(url)
    return zipfile.ZipFile(hf), hf


def members(zf, pattern):
    return [n for n in zf.namelist() if pattern in n and n.endswith(".gml")]
