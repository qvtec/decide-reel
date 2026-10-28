#!/usr/bin/env python3
"""decide-reel: 決めることのJSONから、1ファイルで動くHTMLを作る。

使い方:
    python3 build.py data.json out.html

標準ライブラリだけで動く。できたHTMLは外部への通信をしない。
"""
import argparse
import html
import json
import pathlib
import sys

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
        for j, c in enumerate(it.get("claims") or []):
            if not isinstance(c, dict):
                errs.append("{}.claims[{}]: オブジェクトが必要です".format(w, j))
                continue
            need_str(c.get("source"), "{}.claims[{}].source".format(w, j))
            need_str(c.get("text"), "{}.claims[{}].text".format(w, j))
        need_str_list(it.get("facts"), w + ".facts")
        need_str_list(it.get("unknowns"), w + ".unknowns")
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
            keys.append(o.get("key"))
        if len(set(keys)) != len(keys):
            errs.append("{}.options: key が重複しています".format(w))
        if any(k in ("other", "later") for k in keys):
            errs.append("{}.options: key に other / later は使えません".format(w))
    return errs


# 1カットに大きく出すので、長い文は読み切れない。超えたら警告だけ出す（ビルドは止めない）
LIMITS = {"title": 12, "question": 24, "claim": 20, "source": 10, "fact": 18, "label": 12, "note": 20}


def length_warnings(d):
    warns = []

    def check(text, limit, where):
        if isinstance(text, str) and len(text) > limit:
            warns.append("{}: {}字（目安{}字）「{}」".format(where, len(text), limit, text))

    for i, it in enumerate(d.get("items", [])):
        w = "items[{}]".format(i)
        check(it.get("title"), LIMITS["title"], w + ".title")
        check(it.get("question"), LIMITS["question"], w + ".question")
        claims = it.get("claims") or []
        if len(claims) > 3:
            warns.append("{}.claims: {}件（目安3件）".format(w, len(claims)))
        for j, c in enumerate(claims):
            check(c.get("text"), LIMITS["claim"], "{}.claims[{}].text".format(w, j))
            check(c.get("source"), LIMITS["source"], "{}.claims[{}].source".format(w, j))
        fx = (it.get("facts") or []) + (it.get("unknowns") or [])
        if len(fx) > 4:
            warns.append("{}: facts+unknowns {}件（目安4件）".format(w, len(fx)))
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
    a = ap.parse_args()

    try:
        data = json.loads(pathlib.Path(a.data).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit("JSONを読めません: {}".format(e))
    errs = validate(data)
    if errs:
        sys.exit("データに問題があります:\n  " + "\n  ".join(errs))

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
    warns = length_warnings(data)
    if warns:
        print("長すぎる項目（短くしてビルドし直すと見やすくなります）:\n  " + "\n  ".join(warns), file=sys.stderr)


if __name__ == "__main__":
    main()
