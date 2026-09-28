"""build_map.py が作った形状から、依存ライブラリ無しのSVGを描く。

沖縄は本土から離れているので、そのまま描くと本土が小さくなる。
日本の地図の慣例どおり左下に別枠で置く。

出力は2つ。
- `web/map.svg`      ビューアに埋める用（自治体に data-code を持たせて絞り込みと連動できる）
- `award/deck/map-year.png` 相当は別途PDF化するのでSVGのまま置く
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_map import MAIN, OKI, COLORS, GRAY_FILL, GRAY_LINE, Frame, mercator  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
W = 1000
PAD = 8


def ring_path(ring, frame):
    pts = [frame.to_px(p[0], p[1]) for p in ring]
    return "M%.1f %.1f" % pts[0] + "".join("L%.1f %.1f" % q for q in pts[1:]) + "Z"


def in_frame(ring, frame):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return frame.contains(sum(lons) / len(lons), sum(lats) / len(lats))


def paths(rings, main, oki):
    """本土枠と沖縄枠に振り分けて、それぞれのd属性を返す。"""
    d = ""
    for r in rings:
        if in_frame(r, main):
            d += ring_path(r, main)
        elif in_frame(r, oki):
            d += ring_path(r, oki)
    return d


def build_svg(geom, key, title, sub, crop=None, legend=True, labels=None):
    """crop を渡すとviewBoxだけ差し替えて一部を切り出す（形状は共通）。

    日本全体は縦長（1000x1108）なので、1280x720のスライドに置くと小さくなる。
    主張が一番はっきり出るのは東京（62/62が×）と埼玉（49中36）が接する関東なので、
    そこを横長に切った版を別に出す。
    """
    # 本土の枠：幅Wに収める
    mx0, mytop = mercator(MAIN[0], MAIN[3])   # 左上（北）
    mx1, mybot = mercator(MAIN[2], MAIN[1])   # 右下（南）
    scale = (W - PAD * 2) / (mx1 - mx0)
    H = int((mytop - mybot) * scale) + PAD * 2
    main = Frame(MAIN, scale, PAD, PAD)

    # 沖縄の枠：**縮尺は本土と同じ**にして位置だけ動かす。
    # 縮尺を変えると那覇市の大きさが他の自治体と比べられなくなる。
    ox0, oytop = mercator(OKI[0], OKI[3])
    ox1, oybot = mercator(OKI[2], OKI[1])
    oscale = scale
    ow, oh = (ox1 - ox0) * oscale, (oytop - oybot) * oscale
    # 日本列島は南西から北東に伸びるので左上が大きく空く。凡例と沖縄はそこへ置く。
    # 下端に置くと種子島・屋久島（北緯30.2〜30.8度）と重なる。
    obox_x, obox_y = PAD + 16, 268
    oki = Frame(OKI, oscale, obox_x, obox_y)

    out = []
    vb = "%g %g %g %g" % crop if crop else "0 0 %d %d" % (W, H)
    out.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" '
               'width="100%%" role="img" aria-label="%s">' % (vb, title))

    # 背景：47都道府県の輪郭。県境が見えることが目的
    out.append('<g fill="%s" stroke="%s" stroke-width="0.6" stroke-linejoin="round">' % (GRAY_FILL, GRAY_LINE))
    for pref in sorted(geom["pref"], key=int):
        d = paths(geom["pref"][pref], main, oki)
        if d:
            out.append('<path d="%s"/>' % d)
    out.append('</g>')

    # 前景：PLATEAUがある自治体を段階色で塗る
    out.append('<g stroke="#fff" stroke-width="0.35" stroke-linejoin="round">')
    for code, rings in sorted(geom["city"].items()):
        g = geom[key].get(code, "-")
        d = paths(rings, main, oki)
        if not d:
            continue
        out.append('<path d="%s" fill="%s" data-code="%s"><title>%s %s</title></path>'
                   % (d, COLORS.get(g, "#999"), code, geom["name"].get(code, code), g))
    out.append('</g>')

    # 沖縄インセットの枠線
    if legend:
        out.append('')
    _ = out.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" fill="none" '
               'stroke="%s" stroke-width="0.8" stroke-dasharray="3 3"/>'
               % (obox_x - 4, obox_y - 4, ow + 8, oh + 8, GRAY_LINE))
    out.append('<text x="%.0f" y="%.0f" font-size="10" fill="#8a867e">沖縄県（位置のみ移動・縮尺は本土と同じ）</text>'
               % (obox_x - 4, obox_y - 8))
    if not legend:
        out = [x for x in out if 'stroke-dasharray' not in x and '沖縄県（位置' not in x]

    # 県名のラベル（切り出し版で使う）
    for (lon, lat, txt) in (labels or []):
        x, y = main.to_px(lon, lat)
        out.append('<text x="%.0f" y="%.0f" font-size="7" font-weight="700" fill="#3a3835" '
                   'text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="2.5">'
                   '%s</text>' % (x, y, txt))

    if not legend:
        out.append('</svg>')
        return "\n".join(out)

    # 凡例（左上。右上は北海道が占める）
    lx, ly = PAD + 16, 34
    out.append('<g font-size="11" fill="#3a3835">')
    out.append('<text x="%d" y="%d" font-size="12" font-weight="600">%s</text>' % (lx, ly, sub))
    for i, (g, label) in enumerate([("◎", "90%以上"), ("○", "60〜90%"), ("△", "20〜60%"),
                                    ("▲", "0〜20%"), ("×", "0%")]):
        y = ly + 18 + i * 15
        out.append('<rect x="%d" y="%d" width="11" height="11" fill="%s"/>' % (lx, y - 9, COLORS[g]))
        out.append('<text x="%d" y="%d">%s %s</text>' % (lx + 17, y, g, label))
    y = ly + 18 + 5 * 15
    out.append('<rect x="%d" y="%d" width="11" height="11" fill="%s" stroke="%s"/>'
               % (lx, y - 9, GRAY_FILL, GRAY_LINE))
    out.append('<text x="%d" y="%d">PLATEAU未整備</text>' % (lx + 17, y))
    out.append('</g>')
    # 小笠原村は描画窓の外。黙って落とすと306と数が合わなくなるので断る。
    out.append('<text x="%d" y="%d" font-size="10" fill="#8a867e">'
               '※小笠原村（PLATEAU整備済）は縮尺の都合で図示していない</text>'
               % (PAD + 16, H - 12))
    out.append('</svg>')
    return "\n".join(out)


if __name__ == "__main__":
    geom = json.loads((ROOT / "data" / "map_geom.json").read_text(encoding="utf-8"))

    full = build_svg(geom, "grade", "建築年の充填率を自治体別に塗った日本地図", "建築年の充填率")
    (ROOT / "web" / "map.svg").write_text(full, encoding="utf-8")

    # 関東の切り出し。東京（62/62が×）と埼玉（49中36）が接するところが一番はっきり出る
    kanto = build_svg(geom, "grade", "関東。東京都は全て0%だが隣接する埼玉県は色が付く", "",
                      crop=(505, 636, 270, 178), legend=False,
                      labels=[(139.45, 36.12, "埼玉県"), (139.75, 35.60, "東京都"),
                              (139.35, 35.35, "神奈川県"), (140.25, 35.55, "千葉県"),
                              (139.05, 36.55, "群馬県"), (140.15, 36.45, "茨城県")])
    (ROOT / "web" / "map-kanto.svg").write_text(kanto, encoding="utf-8")

    for f in ("map.svg", "map-kanto.svg"):
        print("-> web/%-14s %5.0f KB" % (f, (ROOT / "web" / f).stat().st_size / 1e3))
    print("   県 %d / 自治体 %d" % (len(geom["pref"]), len(geom["city"])))
