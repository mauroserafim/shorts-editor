"""
Editor automático de Shorts — estilo "Mecanismo Americano" (igual às skills cortar-pausas + editar-shorts).

Layout 1080x1920:
  - Topo (1080x640): B-roll trocando a cada ~2-3 s
  - Baixo (1080x1280): você, enquadrado seguindo o rosto
  - Título amarelo (caixa) na linha divisória nos primeiros segundos
  - Legenda na linha divisória, palavra falada em amarelo
  - Créditos pequenos nos últimos 2 s
Corte: só pausas / trechos sem fala (não corta palavra).

Uso:
    python -m editor.edit entrada.mp4 saida.mp4
Teste:
    --words-json palavras.json   (pula o Whisper)
    --fake-broll                 (b-roll de teste, sem Pexels)
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile

import requests

W, H, FPS = 1080, 1920, 30
TOP_H = 640
BOT_H = H - TOP_H

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
FONT = os.getenv("CAPTION_FONT", "Montserrat ExtraBold")
FONTS_DIR = os.getenv("FONTS_DIR", "")

MAX_GAP = 0.35          # pausa > isso é cortada
PAD = 0.15              # respiro antes/depois da fala
CUT_MIN, CUT_MAX = 1.8, 3.6  # duração de cada b-roll no topo
TITLE_DUR = 3.2


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise RuntimeError("falhou: " + " ".join(cmd[:6]))
    return r.stdout


def probe_duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nk=1:nw=1", path]).strip())


# ------------------------------------------------------------ 1. transcrição
def transcribe(path):
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(path, language="pt", word_timestamps=True, vad_filter=True,
                               condition_on_previous_text=False)
    out = []
    for s in segs:
        for w in s.words or []:
            if w.word.strip():
                out.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return out


# ------------------------------------------------------------ 2. cortar pausas
def cut_pauses(words, duration):
    segs = []
    for w in words:
        s, e = w["start"], w["end"]
        if segs and s - segs[-1][1] <= MAX_GAP:
            segs[-1][1] = max(segs[-1][1], e)
        else:
            segs.append([s, e])
    # respiro sem invadir o vizinho
    out = []
    for i, (s, e) in enumerate(segs):
        prev_e = segs[i - 1][1] if i else 0.0
        next_s = segs[i + 1][0] if i + 1 < len(segs) else duration
        s2 = max(0.0, s - PAD, (prev_e + s) / 2 if i else 0.0)
        e2 = min(duration, e + PAD, (e + next_s) / 2 if i + 1 < len(segs) else duration)
        if e2 - s2 >= 0.2:
            out.append([s2, e2])
    res, t = [], 0.0
    for s, e in out:
        res.append({"src_start": s, "src_end": e, "out_start": t})
        t += e - s
    return res, t


def map_time(t, segs):
    for s in segs:
        if s["src_start"] - 1e-3 <= t <= s["src_end"] + 1e-3:
            return s["out_start"] + min(max(t, s["src_start"]), s["src_end"]) - s["src_start"]
    return None


# ------------------------------------------------------------ 3. rosto
def detect_faces(path, step=0.4):
    """[(t, cx, cy, size)] no vídeo normalizado 1080x1920."""
    import cv2
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    every = max(1, int(round(fps * step)))
    res, i = [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % every == 0:
            ok, fr = cap.retrieve()
            if ok:
                small = cv2.resize(fr, (fr.shape[1] // 3, fr.shape[0] // 3))
                g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                fs = casc.detectMultiScale(g, 1.1, 6, minSize=(50, 50))
                if len(fs):
                    x, y, w, h = max(fs, key=lambda f: f[2] * f[3])
                    res.append((i / fps, (x + w / 2) * 3, (y + h / 2) * 3, w * 3))
        i += 1
    cap.release()
    return res


def crop_for(faces, s, e, src_w, src_h):
    pts = [f for f in faces if s <= f[0] <= e] or faces
    if pts:
        cx = statistics.median(p[1] for p in pts)
        cy = statistics.median(p[2] for p in pts)
        fs = statistics.median(p[3] for p in pts)
    else:
        cx, cy, fs = src_w / 2, src_h * 0.55, src_w * 0.35
    cw = min(src_w, max(src_w * 0.55, fs / 0.68))   # rosto ~68% da largura
    ch = cw * BOT_H / W
    if ch > src_h:
        ch = src_h
        cw = ch * W / BOT_H
    x = min(max(0, cx - cw / 2), src_w - cw)
    y = min(max(0, cy - ch * 0.42), src_h - ch)
    ev = lambda v: int(v) // 2 * 2
    return ev(cw), ev(ch), ev(x), ev(y)


# ------------------------------------------------------------ 4. plano (título + b-rolls) via IA grátis do GitHub
def llm_plan(out_words, total):
    lines, cur, t0 = [], [], None
    for w in out_words:
        if t0 is None:
            t0 = w["start"]
        cur.append(w["word"])
        if w["word"].endswith((".", "?", "!")) or len(cur) >= 12:
            lines.append(f"[{t0:.1f}s] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:.1f}s] {' '.join(cur)}")
    prompt = f"""Você edita Shorts do canal "Mecanismo Americano" (brasileiro mostrando a vida nos EUA).
Transcrição com tempos (duração total {total:.1f}s):
{chr(10).join(lines)}

Responda SÓ um JSON:
{{"title": ["LINHA 1", "LINHA 2"],
  "cuts": [{{"t": 0.0, "q": "english pexels query"}}, ...]}}

Regras:
- title: gancho curioso em MAIÚSCULAS, português, 2 linhas de no máximo 22 caracteres cada, sobre o assunto do vídeo.
- cuts: b-rolls que cobrem o vídeo inteiro. O primeiro em t=0. Cada um dura entre {CUT_MIN} e {CUT_MAX}s até o próximo.
  Sincronize com o que está sendo falado naquele instante.
- q: 2 a 4 palavras EM INGLÊS descrevendo uma cena concreta filmável de banco de vídeo (ex: "factory worker assembly line",
  "family road trip car", "counting dollar bills", "brazil favela aerial"). Nada de nomes de pessoas ou marcas. Varie as cenas."""
    plan, last_err = None, None
    attempts = []
    if GEMINI_KEY:
        attempts += [("gemini", m) for m in ("gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash")]
    if GH_TOKEN:
        attempts += [("github", m) for m in (LLM_MODEL, "openai/gpt-4o-mini")]
    for provider, model in attempts:
        try:
            content = ask_gemini(model, prompt) if provider == "gemini" else ask_github(model, prompt)
            m = re.search(r"\{.*\}", content, re.S)
            if not m:
                raise RuntimeError(f"sem JSON: {content[:300]!r}")
            plan = json.loads(m.group(0))
            print(f"IA ok ({provider} {model})", flush=True)
            break
        except Exception as ex:
            last_err = ex
            print(f"  IA {provider} {model}: {ex}", flush=True)
    if not plan:
        raise RuntimeError(last_err or "nenhuma IA configurada")
    cuts = sorted(({"t": float(c["t"]), "q": str(c["q"])} for c in plan["cuts"]), key=lambda c: c["t"])
    title = [str(x).upper() for x in plan.get("title", [])][:2]
    return title, cuts


def ask_gemini(model, prompt):
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      params={"key": GEMINI_KEY},
                      json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}},
                      timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def ask_github(model, prompt):
    r = requests.post("https://models.github.ai/inference/chat/completions",
                      headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json",
                               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                      json={"model": model, "temperature": 0.4,
                            "messages": [{"role": "user", "content": prompt}]}, timeout=120)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"HTTP {r.status_code} {r.headers.get('content-type')}: {r.text[:200]!r}")
    return r.json()["choices"][0]["message"]["content"] or ""


STOP = set("""a o as os um uma de da do das dos em no na nos nas num numa por pra para pro com que se e é ou
mas mais muito já não eu ele ela eles você isso esse essa aqui ali lá aí então tipo né também como quando
porque ser ter tem foi era são está vai cada mesmo cara vez bem até sobre todo todos lado""".split())


def fallback_plan(out_words, total):
    cuts, t = [], 0.0
    while t < total - 0.5:
        ws = [re.sub(r"[^\wà-ü]", "", w["word"].lower()) for w in out_words if t <= w["start"] < t + 3]
        ws = sorted({x for x in ws if len(x) > 3 and x not in STOP}, key=len, reverse=True)
        cuts.append({"t": t, "q": " ".join(ws[:2]) or "united states city", "pt": True})
        t += 2.8
    return ["VOCÊ PRECISA", "VER ISSO"], cuts


def normalize_cuts(cuts, total):
    out = []
    for c in cuts:
        t = max(0.0, min(c["t"], total))
        if out and t - out[-1]["t"] < CUT_MIN:
            continue
        out.append({**c, "t": t})
    if not out or out[0]["t"] > 0:
        out.insert(0, {"t": 0.0, "q": (out[0]["q"] if out else "united states city")})
    res = []
    for i, c in enumerate(out):
        end = out[i + 1]["t"] if i + 1 < len(out) else total
        # quebra trechos longos demais
        while end - c["t"] > CUT_MAX + 0.5:
            res.append({**c, "dur": CUT_MAX * 0.8})
            c = {**c, "t": c["t"] + CUT_MAX * 0.8, "alt": True}
        res.append({**c, "dur": end - c["t"]})
    return res


# ------------------------------------------------------------ 5. b-roll Pexels (paisagem, topo)
def pexels_video(q, used, pt=False):
    params = {"query": q, "orientation": "landscape", "per_page": 15, "size": "medium"}
    if pt:
        params["locale"] = "pt-BR"
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                     params=params, timeout=30)
    if r.status_code != 200:
        return None
    for v in r.json().get("videos", []):
        if v["id"] in used or v.get("duration", 0) < 4:
            continue
        files = [f for f in v.get("video_files", []) if f.get("width") and f["width"] >= 1280
                 and f.get("file_type") == "video/mp4"]
        if not files:
            continue
        files.sort(key=lambda f: abs(f["width"] - 1920))
        used.add(v["id"])
        return files[0]["link"]
    return None


def make_top_clip(cut, used, tmp, i, fake=False):
    outp = os.path.join(tmp, f"top{i:03d}.mp4")
    frames = max(1, round(cut["dur"] * FPS))
    if not fake:
        link = None
        q = cut["q"]
        for qq in (q, " ".join(q.split()[:2]), q.split()[0]):
            link = pexels_video(qq, used, cut.get("pt", False))
            if link:
                break
        if link:
            raw = os.path.join(tmp, f"raw{i:03d}.mp4")
            with requests.get(link, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(raw, "wb") as f:
                    for ch in r.iter_content(1 << 20):
                        f.write(ch)
            run(["ffmpeg", "-y", "-ss", "0.5", "-stream_loop", "-1", "-i", raw, "-frames:v", str(frames), "-an",
                 "-vf", f"scale={W}:{TOP_H}:force_original_aspect_ratio=increase,crop={W}:{TOP_H},fps={FPS},setsar=1",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", outp])
            return outp
    txt = re.sub(r"[^A-Za-z0-9 ]", "", cut["q"])[:30]
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{TOP_H}:r={FPS}", "-frames:v", str(frames),
         "-vf", f"drawtext=text='{txt}':fontsize=50:fontcolor=white:x=(w-tw)/2:y=60:box=1:boxcolor=black",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", outp])
    return outp


# ------------------------------------------------------------ 6. legendas / título / créditos (ASS)
def ass_time(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean(w):
    w = w.strip().strip("\"“”'«»")
    w = re.sub(r"[,.;:…]+$", "", w).replace("--", "")
    return w.upper()


def build_ass(out_words, title, total, credits, path):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,2,5,30,30,0,1
Style: Title,{FONT},84,&H00000000,&H00000000,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,0,0,5,20,20,0,1
Style: Box,{FONT},20,&H0000CCFF,&H0000CCFF,&H0000CCFF,&H0000CCFF,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Cred,{FONT},32,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,28,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    title_end = min(TITLE_DUR, total)
    if title:
        bh = 34 + 84 * len(title)
        y0 = 665 - bh  # caixa termina logo abaixo da linha divisória
        ev.append(f"Dialogue: 2,{ass_time(0)},{ass_time(title_end)},Box,,0,0,0,,"
                  + r"{\pos(45," + str(y0) + r")\fad(250,150)\p1}" + f"m 0 0 l 990 0 990 {bh} 0 {bh}" + r"{\p0}")
        ev.append(f"Dialogue: 3,{ass_time(0)},{ass_time(title_end)},Title,,0,0,0,,"
                  + r"{\pos(540," + str(y0 + bh // 2) + r")\fad(250,150)}" + r"\N".join(title))
    # legendas: 1-3 palavras, só depois do título
    words = [w for w in out_words if w["end"] > title_end]
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if (len(cur) >= 3 or nxt is None or nxt["start"] - w["end"] > 0.3
                or w["word"].endswith((".", "?", "!", ","))):
            groups.append(cur)
            cur = []
    for gi, g in enumerate(groups):
        nxt_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.4
        texts = [clean(w["word"]) for w in g]
        for wi, w in enumerate(g):
            st = max(w["start"], title_end)
            en = g[wi + 1]["start"] if wi + 1 < len(g) else min(nxt_start, g[-1]["end"] + 0.5)
            if en - st < 0.03:
                continue
            parts = [(r"{\c&H00D4FF&}" + t + r"{\c&HFFFFFF&}") if k == wi else t for k, t in enumerate(texts)]
            ev.append(f"Dialogue: 1,{ass_time(st)},{ass_time(en)},Cap,,0,0,0,,"
                      r"{\pos(540,640)}" + " ".join(parts))
    if credits:
        ev.append(f"Dialogue: 4,{ass_time(max(0, total - 2))},{ass_time(total)},Cred,,0,0,0,,"
                  + r"\N".join(credits))
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(ev) + "\n")


# ------------------------------------------------------------ 7. render
def edit(src, out, words_json=None, fake_broll=False):
    tmp = tempfile.mkdtemp(prefix="short_")
    try:
        duration = probe_duration(src)
        norm = os.path.join(tmp, "norm.mp4")
        # celular grava em HDR (HLG/PQ): converte pra SDR com tonemap, senão fica escuro/lavado
        trc = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
                   "-of", "default=nk=1:nw=1", src]).strip()
        tm = ""
        if trc in ("smpte2084", "arib-std-b67"):
            print(f"Vídeo HDR ({trc}): convertendo cores para SDR", flush=True)
            tm = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:param=0.3:desat=0,"
                  "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")
        run(["ffmpeg", "-y", "-i", src, "-vf", f"{tm}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", norm])

        if words_json:
            words = json.load(open(words_json, encoding="utf-8"))
        else:
            print("Transcrevendo…", flush=True)
            words = transcribe(norm)
        if not words:
            raise RuntimeError("Nenhuma fala encontrada.")

        segs, total = cut_pauses(words, duration)
        print(f"Pausas: {duration:.1f}s -> {total:.1f}s ({len(segs)} trechos)", flush=True)
        out_words = []
        for w in words:
            s, e = map_time(w["start"], segs), map_time(w["end"], segs)
            if s is not None and e is not None:
                out_words.append({"word": w["word"], "start": s, "end": max(e, s + 0.05)})

        # baixo: você, enquadrado no rosto
        faces = detect_faces(norm)
        print(f"Rosto detectado em {len(faces)} amostras", flush=True)
        parts = []
        for i, s in enumerate(segs):
            d = s["src_end"] - s["src_start"]
            cw, ch, x, y = crop_for(faces, s["src_start"], s["src_end"], W, H)
            p = os.path.join(tmp, f"bot{i:03d}.mp4")
            fade = min(0.02, d / 4)
            run(["ffmpeg", "-y", "-ss", f"{s['src_start']:.3f}", "-i", norm, "-t", f"{d:.3f}",
                 "-vf", f"crop={cw}:{ch}:{x}:{y},scale={W}:{BOT_H},setsar=1",
                 "-af", f"afade=t=in:d={fade},afade=t=out:st={d - fade:.3f}:d={fade}",
                 "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", p])
            parts.append(p)
        bottom = os.path.join(tmp, "bottom.mp4")
        with open(os.path.join(tmp, "b.txt"), "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(tmp, "b.txt"), "-c", "copy", bottom])
        total = probe_duration(bottom)

        # plano: título + b-rolls
        title, cuts, plan_src = None, None, "ia"
        if GEMINI_KEY or GH_TOKEN:
            try:
                title, cuts = llm_plan(out_words, total)
            except Exception as ex:
                print(f"IA falhou ({ex}); usando plano simples", flush=True)
        if not cuts:
            title, cuts = fallback_plan(out_words, total)
            plan_src = "simples"
        cuts = normalize_cuts(cuts, total)
        print(f"Título: {' / '.join(title)}", flush=True)

        # topo: b-rolls
        used, tops = set(), []
        for i, c in enumerate(cuts):
            try:
                tops.append(make_top_clip(c, used, tmp, i, fake=fake_broll or not PEXELS_KEY))
            except Exception as ex:
                print(f"  b-roll '{c['q']}' falhou: {ex}", flush=True)
                tops.append(make_top_clip(c, used, tmp, i, fake=True))
            print(f"  {c['t']:5.1f}s  {c['q']}", flush=True)
        top = os.path.join(tmp, "top.mp4")
        with open(os.path.join(tmp, "t.txt"), "w") as f:
            f.writelines(f"file '{p}'\n" for p in tops)
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(tmp, "t.txt"), "-c", "copy", top])

        ass = os.path.join(tmp, "caps.ass")
        build_ass(out_words, title, total, ["Vídeos: Pexels"], ass)
        ass_esc = ass.replace("\\", "/").replace(":", "\\:")
        sub = f"subtitles='{ass_esc}'" + (f":fontsdir='{FONTS_DIR}'" if FONTS_DIR else "")
        run(["ffmpeg", "-y", "-i", bottom, "-i", top, "-filter_complex",
             f"[1:v]tpad=stop_mode=clone:stop_duration=5[t];[t][0:v]vstack=inputs=2:shortest=1,{sub},format=yuv420p[v]",
             "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-r", str(FPS),
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])

        return {"duracao_original": round(duration, 1), "duracao_final": round(total, 1),
                "trechos": len(segs), "titulo": title, "plano": plan_src,
                "brolls": [{"t": round(c["t"], 1), "busca": c["q"]} for c in cuts]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--words-json")
    ap.add_argument("--fake-broll", action="store_true")
    a = ap.parse_args()
    print(json.dumps(edit(a.src, a.out, a.words_json, a.fake_broll), ensure_ascii=False, indent=2))
