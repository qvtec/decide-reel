"""decide-reel: 読み上げる文を、ビルドのときにクラウドの声で音声にする。

できた音声はHTMLに埋め込むので、ページを開くときは外部への通信をしない。
APIキーは環境変数から読み、ページには入れない。

  gcloud      Google Cloud Text-to-Speech
              GOOGLE_TTS_API_KEY（なければ `gcloud auth print-access-token` を使う）
  elevenlabs  ElevenLabs
              ELEVENLABS_API_KEY、声は ELEVENLABS_VOICE_ID か設定の voice

キーは ~/.config/decide-reel/env（`export NAME=値` か `NAME=値` の行）にも書いておける。
リポジトリの外に置くので、どのプロジェクトで動かしても使え、誤ってコミットすることもない。
環境変数が設定されていれば、そちらを優先する。

作った音声は data.json の隣の .decide-reel-cache/ にとっておき、同じ文は作り直さない。
"""
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request

PROVIDERS = ("browser", "gcloud", "elevenlabs")
LABELS = {"gcloud": "Google", "elevenlabs": "ElevenLabs"}
DEFAULT_VOICE = {
    ("gcloud", "ja"): "ja-JP-Neural2-B",
    ("gcloud", "en"): "en-US-Neural2-F",
    # ElevenLabs の既製の声（Rachel）。多言語のモデルなので日本語も読む。日本語の声は ElevenLabs の Voice Library で選ぶ
    ("elevenlabs", "ja"): "21m00Tcm4TlvDq8ikWAM",
    ("elevenlabs", "en"): "21m00Tcm4TlvDq8ikWAM",
}


# まとめの画面の締めの一言。template.html の sayClosing と揃える
CLOSING = {"ja": "おつかれさまでした。決めたことをまとめました。", "en": "Nice work. Here is what we decided."}


ENV_FILE = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "decide-reel" / "env"


def load_env_file(path=ENV_FILE):
    """キーのファイルを読み、まだ設定されていない環境変数だけを足す"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        if sep and name and name not in os.environ:
            os.environ[name] = value


DEFAULT_MODEL = {"elevenlabs": "eleven_v4"}


class TTSError(Exception):
    pass


def texts(d):
    """ページが読み上げる文を、ページと同じ組み立て方で並べる（template.html の beatsOf・sayOpt と揃える）"""
    en = str(d.get("lang") or "ja").lower().startswith("en")
    out = []
    for it in d["items"]:
        out.append(it.get("say") or it["question"])
        if it.get("narrate"):
            out.append(it["narrate"])
        for o in it["options"]:
            out.append(o.get("say") or o["label"])
    out.append(d.get("closing") or CLOSING["en" if en else "ja"])
    return list(dict.fromkeys(out))


def post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise TTSError("HTTP {}: {}".format(e.code, detail))
    except urllib.error.URLError as e:
        raise TTSError("つながりません: {}".format(e.reason))


def gcloud_cmd(*args):
    try:
        return subprocess.run(["gcloud"] + list(args), capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def gcloud_synth(cfg, lang):
    voice = cfg["voice"]
    url = "https://texttospeech.googleapis.com/v1/text:synthesize"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if key:
        headers["X-Goog-Api-Key"] = key
    else:
        token = gcloud_cmd("auth", "print-access-token")
        if not token:
            raise TTSError("GOOGLE_TTS_API_KEY がなく、gcloud でもログインしていません（`gcloud auth login`）")
        headers["Authorization"] = "Bearer " + token
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or gcloud_cmd("config", "get-value", "project")
        if project:
            headers["x-goog-user-project"] = project

    def synth(text):
        body = {
            "input": {"text": text},
            "voice": {"languageCode": "-".join(voice.split("-")[:2]), "name": voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": cfg["rate"]},
        }
        res = json.loads(post(url, headers, body))
        if "audioContent" not in res:
            raise TTSError("音声が返ってきませんでした")
        return base64.b64decode(res["audioContent"])
    return synth


def elevenlabs_synth(cfg, lang):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise TTSError("ELEVENLABS_API_KEY がありません")
    url = "https://api.elevenlabs.io/v1/text-to-speech/{}?output_format=mp3_44100_64".format(cfg["voice"])
    headers = {"Content-Type": "application/json", "xi-api-key": key, "Accept": "audio/mpeg"}

    def synth(text):
        body = {"text": text, "model_id": cfg["model"],
                "voice_settings": {"speed": max(0.7, min(1.2, cfg["rate"]))}}
        return post(url, headers, body)
    return synth


SYNTH = {"gcloud": gcloud_synth, "elevenlabs": elevenlabs_synth}


def settings(d, provider=None, voice=None):
    """CLIの指定 → JSONの tts → 環境変数 DECIDE_REEL_TTS の順に、使う声を決める"""
    load_env_file()
    cfg = d.get("tts") if isinstance(d.get("tts"), dict) else {}
    p = provider or cfg.get("provider") or os.environ.get("DECIDE_REEL_TTS") or "browser"
    if p not in PROVIDERS:
        raise TTSError("tts の provider は {} のどれかにしてください".format(" / ".join(PROVIDERS)))
    lang = "en" if str(d.get("lang") or "ja").lower().startswith("en") else "ja"
    env_voice = os.environ.get("ELEVENLABS_VOICE_ID") if p == "elevenlabs" else None
    return {
        "provider": p,
        "lang": lang,
        "voice": voice or cfg.get("voice") or env_voice or DEFAULT_VOICE.get((p, lang)),
        # キャッシュの名前に入るので、既定のモデルもここで決めておく
        "model": cfg.get("model") or DEFAULT_MODEL.get(p),
        "rate": float(cfg.get("rate") or d.get("rate") or 1.05),
    }


def synthesize(d, cfg, cache_dir):
    """読み上げる文をすべて音声にし、{文: data URI} と、作った本数・キャッシュから出した本数を返す"""
    synth = SYNTH[cfg["provider"]](cfg, cfg["lang"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    clips, made, cached = {}, 0, 0
    for text in texts(d):
        tag = json.dumps([cfg["provider"], cfg["voice"], cfg["model"], cfg["rate"], text], ensure_ascii=False)
        f = cache_dir / (hashlib.sha256(tag.encode("utf-8")).hexdigest()[:24] + ".mp3")
        if f.exists():
            audio, cached = f.read_bytes(), cached + 1
        else:
            audio, made = synth(text), made + 1
            f.write_bytes(audio)
        clips[text] = "data:audio/mpeg;base64," + base64.b64encode(audio).decode("ascii")
    return clips, made, cached
