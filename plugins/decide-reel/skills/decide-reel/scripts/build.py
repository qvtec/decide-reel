#!/usr/bin/env python3
"""decide-reel: 決めることのJSONから、1ファイルで動くHTMLを作る。

使い方:
    python3 build.py data.json out.html
    python3 build.py data.json out.html --tts gcloud      # Google Cloud TTS の声を埋め込む
    python3 build.py data.json out.html --tts elevenlabs  # ElevenLabs の声を埋め込む

標準ライブラリだけで動く。できたHTMLは外部への通信をしない。
"""
import argparse
import html
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import tts

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "template.html"
PLACEHOLDER = "__DECIDE_REEL_DATA__"


def validate(d):
    errs = []

    def need_str(v, where):
        if not isinstance(v, str) or not v.strip():
            errs.append("{}: 空でない文字列が必要です".format(where))

    def need_str_list(v, where):
        if v is None:
            return
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            errs.append("{}: 文字列の配列が必要です".format(where))

    if not isinstance(d, dict):
        return ["トップレベルはオブジェクトにしてください"]
    need_str(d.get("title"), "title")
    if "closing" in d:
        need_str(d.get("closing"), "closing")
    if d.get("theme") is not None and d.get("theme") not in THEMES:
        errs.append("theme: {} のどれかにしてください".format(" / ".join(THEMES)))
    items = d.get("items")
    if not isinstance(items, list) or not items:
        errs.append("items: 1件以上の配列が必要です")
        return errs
    for i, it in enumerate(items):
        w = "items[{}]".format(i)
        if not isinstance(it, dict):
            errs.append("{}: オブジェクトが必要です".format(w))
            continue
        need_str(it.get("title"), w + ".title")
        need_str(it.get("question"), w + ".question")
        if "narrate" in it:
            need_str(it.get("narrate"), w + ".narrate")
        for j, c in enumerate(it.get("claims") or []):
            if not isinstance(c, dict):
                errs.append("{}.claims[{}]: オブジェクトが必要です".format(w, j))
                continue
            need_str(c.get("source"), "{}.claims[{}].source".format(w, j))
            need_str(c.get("text"), "{}.claims[{}].text".format(w, j))
        need_str_list(it.get("facts"), w + ".facts")
        need_str_list(it.get("unknowns"), w + ".unknowns")
        if it.get("figure") is not None:
            errs.extend(validate_figure(it["figure"], w + ".figure"))
        opts = it.get("options")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
            errs.append("{}.options: 2〜4件の配列が必要です".format(w))
            continue
        keys = []
        for j, o in enumerate(opts):
            if not isinstance(o, dict):
                errs.append("{}.options[{}]: オブジェクトが必要です".format(w, j))
                continue
            need_str(o.get("key"), "{}.options[{}].key".format(w, j))
            need_str(o.get("label"), "{}.options[{}].label".format(w, j))
            if "say" in o:
                need_str(o.get("say"), "{}.options[{}].say".format(w, j))
            if "icon" in o:
                errs.extend(need_icon(o.get("icon"), "{}.options[{}].icon".format(w, j)))
            keys.append(o.get("key"))
        if len(set(keys)) != len(keys):
            errs.append("{}.options: key が重複しています".format(w))
        if any(k in ("other", "later") for k in keys):
            errs.append("{}.options: key に other / later は使えません".format(w))
    return errs


THEMES = ("pop", "navy", "mono", "natural", "slate", "midnight", "carbon", "keynote")
FIGURES = ("bars", "donut", "flow", "stats", "svg")

# ページにそのまま埋め込むので、絵を描く要素だけを通す
SVG_TAGS = {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon",
            "text", "tspan", "defs", "lineargradient", "radialgradient", "stop", "title", "desc"}


def check_svg(src, where):
    """SVGの文字列を検査し、問題があればエラーの文を返す"""
    if "<" not in src:
        return []  # 絵文字
    if not re.match(r"\s*<svg[\s>]", src, re.I):
        return ["{}: 絵文字か、<svg> で始まる文字列にしてください".format(where)]
    if "<?" in src or "<!" in src:
        return ["{}: <? や <! は使えません".format(where)]
    try:
        root = ET.fromstring(src)
    except ET.ParseError as e:
        return ["{}: SVGとして読めません（{}）".format(where, e)]
    errs = []
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in SVG_TAGS:
            errs.append("{}: <{}> は使えません".format(where, tag))
        for k, v in node.attrib.items():
            name = k.split("}")[-1].lower()
            if name.startswith("on") or name == "href":
                errs.append("{}: {} 属性は使えません".format(where, name))
            elif "image-set(" in v.lower() or ("url(" in v.lower() and not re.fullmatch(r"\s*url\(#[\w-]+\)\s*", v)):
                errs.append("{}: 外部を指す url() は使えません".format(where))
    return errs


def need_icon(v, where):
    if not isinstance(v, str) or not v.strip():
        return ["{}: 空でない文字列が必要です".format(where)]
    return check_svg(v, where)


def validate_figure(f, w):
    if not isinstance(f, dict) or f.get("type") not in FIGURES:
        return ["{}: type は {} のどれかにしてください".format(w, " / ".join(FIGURES))]
    errs = []
    if f["type"] == "svg":
        if not isinstance(f.get("svg"), str) or not re.match(r"\s*<svg[\s>]", f["svg"], re.I):
            return ["{}.svg: <svg> で始まる文字列が必要です".format(w)]
        return check_svg(f["svg"], w + ".svg")
    if f["type"] == "flow":
        steps = f.get("steps")
        if not isinstance(steps, list) or not 2 <= len(steps) <= 6:
            return ["{}.steps: 2〜6件の配列が必要です".format(w)]
        for j, s in enumerate(steps):
            label = s if isinstance(s, str) else s.get("label") if isinstance(s, dict) else None
            if not isinstance(label, str) or not label.strip():
                errs.append("{}.steps[{}]: 文字列か {{\"label\": ...}} が必要です".format(w, j))
            elif isinstance(s, dict) and "icon" in s:
                errs.extend(need_icon(s["icon"], "{}.steps[{}].icon".format(w, j)))
        return errs
    data = f.get("data")
    if not isinstance(data, list) or not data:
        return ["{}.data: 1件以上の配列が必要です".format(w)]
    for j, x in enumerate(data):
        where = "{}.data[{}]".format(w, j)
        if not isinstance(x, dict) or not isinstance(x.get("label"), str):
            errs.append(where + ": label のあるオブジェクトが必要です")
            continue
        if "icon" in x:
            errs.extend(need_icon(x["icon"], where + ".icon"))
        v = x.get("value")
        if f["type"] == "stats":
            if not isinstance(v, (int, float, str)) or isinstance(v, bool) or str(v).strip() == "":
                errs.append(where + ".value: 数か文字列が必要です")
        elif not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            errs.append(where + ".value: 0以上の数が必要です")
    return errs


# 1カットに大きく出すので、長い文は読み切れない。超えたら警告だけ出す（ビルドは止めない）
LIMITS = {"title": 12, "question": 24, "claim": 16, "source": 8, "fact": 16, "label": 10, "note": 14, "figlabel": 8, "narrate": 40}


def length_warnings(d):
    warns = []

    def check(text, limit, where):
        if isinstance(text, str) and len(text) > limit:
            warns.append("{}: {}字（目安{}字）「{}」".format(where, len(text), limit, text))

    for i, it in enumerate(d.get("items", [])):
        w = "items[{}]".format(i)
        check(it.get("title"), LIMITS["title"], w + ".title")
        check(it.get("question"), LIMITS["question"], w + ".question")
        check(it.get("narrate"), LIMITS["narrate"], w + ".narrate")
        claims = it.get("claims") or []
        fx = (it.get("facts") or []) + (it.get("unknowns") or [])
        cap = 2 if it.get("figure") else 3
        if len(claims) + len(fx) > cap:
            warns.append("{}: claims+facts+unknowns {}件（目安{}件{}）".format(
                w, len(claims) + len(fx), cap, "。図があるとき" if cap == 2 else ""))
        for j, c in enumerate(claims):
            check(c.get("text"), LIMITS["claim"], "{}.claims[{}].text".format(w, j))
            check(c.get("source"), LIMITS["source"], "{}.claims[{}].source".format(w, j))
        f = it.get("figure") or {}
        if f.get("type") == "svg" and len(f.get("svg", "")) > 8000:
            warns.append("{}.figure.svg: {}字（目安8000字。図を簡単にする）".format(w, len(f["svg"])))
        figs = [s if isinstance(s, str) else s.get("label") for s in f.get("steps") or []] + [x.get("label") for x in f.get("data") or []]
        if len(figs) > (3 if f.get("type") == "stats" else 5):
            warns.append("{}.figure: {}件（目安{}件）".format(w, len(figs), 3 if f.get("type") == "stats" else 5))
        for j, t in enumerate(figs):
            check(t, LIMITS["figlabel"], "{}.figure[{}].label".format(w, j))
        for j, f in enumerate(fx):
            check(f, LIMITS["fact"], "{}.facts/unknowns[{}]".format(w, j))
        for j, o in enumerate(it.get("options") or []):
            check(o.get("label"), LIMITS["label"], "{}.options[{}].label".format(w, j))
            check(o.get("note"), LIMITS["note"], "{}.options[{}].note".format(w, j))
    return warns


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", help="決めることのJSON")
    ap.add_argument("out", help="書き出すHTML")
    ap.add_argument("--tts", choices=tts.PROVIDERS, help="声の作り方（既定は JSON の tts.provider、なければブラウザの読み上げ）")
    ap.add_argument("--voice", help="クラウドの声の名前（gcloud）か ID（elevenlabs）")
    a = ap.parse_args()

    try:
        data = json.loads(pathlib.Path(a.data).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit("JSONを読めません: {}".format(e))
    errs = validate(data)
    if errs:
        sys.exit("データに問題があります:\n  " + "\n  ".join(errs))

    # クラウドの声。作れなかったときはブラウザの読み上げで動くので、ビルドは止めない
    voice_note = ""
    try:
        cfg = tts.settings(data, a.tts, a.voice)
    except tts.TTSError as e:
        sys.exit(str(e))
    if cfg["provider"] != "browser":
        try:
            clips, made, cached = tts.synthesize(data, cfg, pathlib.Path(a.data).resolve().parent / ".decide-reel-cache")
            data = dict(data, audio=clips, audioLabel=tts.LABELS[cfg["provider"]])
            size = sum(len(c) for c in clips.values()) * 3 // 4 // 1024
            voice_note = "声: {} {}{}（{}本・約{}KB、新しく作った {}本）".format(
                tts.LABELS[cfg["provider"]], cfg["voice"], " / " + cfg["model"] if cfg["model"] else "", len(clips), size, made)
        except tts.TTSError as e:
            print("クラウドの声を作れませんでした（ブラウザの読み上げで動きます）: {}".format(e), file=sys.stderr)

    # <script> の中に埋め込むので、</script> で閉じられないようにしておく
    payload = (json.dumps(data, ensure_ascii=False)
               .replace("</", "<\\/")
               .replace(" ", "\\u2028")
               .replace(" ", "\\u2029"))
    page = TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("<title>decide-reel</title>", "<title>{}</title>".format(html.escape(data["title"].replace("\n", ""))), 1)
    page = page.replace(PLACEHOLDER, payload, 1)

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print("{} ({}件)".format(out.resolve(), len(data["items"])))
    if voice_note:
        print(voice_note)
    warns = length_warnings(data)
    if warns:
        print("長すぎる項目（短くしてビルドし直すと見やすくなります）:\n  " + "\n  ".join(warns), file=sys.stderr)


if __name__ == "__main__":
    main()
