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
from PIL import Image

W, H, FPS = 1080, 1920, 30
TOP_H = 640
BOT_H = H - TOP_H

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
# vários modelos: se um estiver ocupado/sem cota, tenta o próximo (os "lite" têm cota grátis maior)
GEMINI_MODELS = (os.getenv("GEMINI_MODELS") or "gemini-3.8-flash,gemini-flash-lite-latest,gemini-flash-latest").split(",")
_GEMINI_OK = []
_GEMINI_DEAD = set()   # modelos que já deram "sem cota" nesta execução: não insiste (economiza tempo)
# Reserva grátis: Groq (sem cartão). Só entra se o Gemini falhar. Chave no secret GROQ_API_KEY.
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_TEXT_MODELS = (os.getenv("GROQ_TEXT_MODELS") or
                    "qwen/qwen3.6-27b,llama-3.3-70b-versatile,meta-llama/llama-4-scout-17b-16e-instruct").split(",")
GROQ_VISION_MODELS = (os.getenv("GROQ_VISION_MODELS") or
                      "qwen/qwen3.6-27b,meta-llama/llama-4-scout-17b-16e-instruct").split(",")
FONT = os.getenv("CAPTION_FONT", "Montserrat ExtraBold")
FONTS_DIR = os.getenv("FONTS_DIR", "")

MAX_GAP = 0.35          # pausa > isso é cortada
PAD = 0.15              # respiro antes/depois da fala
CUT_MIN, CUT_MAX = 1.8, 3.6  # duração de cada b-roll no topo
TITLE_DUR = 3.0
SHOW_TITLE = (os.getenv("SHOW_TITLE") or "1") == "1"  # sem título: legenda já começa no primeiro segundo


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


ZOOM_LEVELS = [1.0, 1.14, 1.04, 1.24]   # aberto, médio, aberto, fechado
PUSH = 0.04                              # aproximação lenta durante o plano
PUNCH = 0.05                             # "soco" de zoom no início de cada plano
SHOT_MIN, SHOT_MAX = 2.2, 3.6


def make_shots(segs, words):
    """Divide os trechos em planos curtos, cortando sempre entre palavras."""
    shots = []
    for sg in segs:
        s0, s1 = sg["src_start"], sg["src_end"]
        gaps = [(words[k]["end"] + words[k + 1]["start"]) / 2 for k in range(len(words) - 1)
                if s0 < words[k]["end"] and words[k + 1]["start"] < s1]
        t = s0
        while s1 - t > SHOT_MAX + 0.5:
            cand = [g for g in gaps if t + SHOT_MIN <= g <= t + SHOT_MAX]
            if not cand:
                cand = [g for g in gaps if g > t + SHOT_MIN]
                if not cand or s1 - cand[0] < 1.0:
                    break
            cut = cand[-1] if cand[-1] <= t + SHOT_MAX else cand[0]
            shots.append({"start": t, "end": cut})
            t = cut
        shots.append({"start": t, "end": s1})
    return shots


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
    cw = min(src_w, max(src_w * 0.6, fs / 0.58))   # rosto ~58% da largura (mostra a cabeça inteira)
    ch = cw * BOT_H / W
    if ch > src_h:
        ch = src_h
        cw = ch * W / BOT_H
    x = min(max(0, cx - cw / 2), src_w - cw)
    y = min(max(0, cy - ch * 0.36), src_h - ch)
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
  "cuts": [{{"t": 0.0, "q": ["query 1", "query 2", "query 3"]}}, ...]}}

Regras:
- title: título curto de abertura, numa linha só, em MAIÚSCULAS, português, de 2 a 5 palavras e no máximo 22 caracteres.
  Resuma a promessa do vídeo com curiosidade ou contraste, como os Shorts com milhões de views.
  Ex: ["BRASIL X EUA"], ["O CARRO DO OPERÁRIO"], ["ISSO É NORMAL AQUI"], ["GASOLINA NOS EUA"].
- cuts: b-rolls que cobrem o vídeo inteiro. O primeiro em t=0. Cada um dura entre {CUT_MIN} e {CUT_MAX}s até o próximo.
  Sincronize com o que está sendo falado naquele instante.
- q: 3 buscas EM INGLÊS para o banco de vídeos Pexels, da mais específica para a mais genérica, cada uma com 2 a 5 palavras.
  Pense como um editor profissional: cenas cinematográficas, concretas, com gente real e emoção, que ilustrem exatamente a
  frase (ex: para "o cara não tinha carro, pegava ônibus" -> ["worker waiting crowded bus stop", "people boarding city bus",
  "bus stop morning"]). Quando falar do Brasil use "brazil"/"brazilian"/"latin america"; dos EUA, "american"/"usa".
  Nada de nomes de pessoas ou marcas. Não repita cenas.
"""
    plan, last_err = None, None
    attempts = []
    if GEMINI_KEY:
        attempts += [("gemini", m) for m in GEMINI_MODELS if m not in _GEMINI_DEAD]
    if GROQ_KEY:
        attempts += [("groq", m) for m in GROQ_TEXT_MODELS]
    for provider, model in attempts:
        try:
            content = ask_gemini(model, prompt) if provider == "gemini" else ask_groq(model, prompt)
            plan = parse_json(content)
            if not plan.get("cuts"):
                raise RuntimeError(f"sem cortes: {content[:200]!r}")
            print(f"IA ok ({provider} {model})", flush=True)
            if provider == "gemini":
                _GEMINI_OK.insert(0, model)
            break
        except Exception as ex:
            last_err = ex
            print(f"  IA {provider} {model}: {ex}", flush=True)
    if not plan:
        raise RuntimeError(last_err or "nenhuma IA configurada")
    cuts = []
    for c in plan["cuts"]:
        qs = c["q"] if isinstance(c["q"], list) else [c["q"]]
        qs = [str(q) for q in qs if str(q).strip()][:3]
        if qs:
            cuts.append({"t": float(c["t"]), "q": qs[0], "qs": qs})
    cuts.sort(key=lambda c: c["t"])
    title = [str(x).upper().replace("**", "*") for x in plan.get("title", [])][:2]
    return title, cuts


def parse_json(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    raw = m.group(0) if m else txt
    try:
        return json.loads(raw)
    except Exception:
        from json_repair import repair_json  # conserta JSON com vírgula faltando/cortado
        return json.loads(repair_json(raw))


def ask_gemini(model, prompt):
    import time
    gen = {"temperature": 0.4, "responseMimeType": "application/json", "maxOutputTokens": 8192,
           "thinkingConfig": {"thinkingLevel": "low"}}  # sem "pensar" muito: resposta rápida
    r = None
    for wait in (0, 30, 90):  # 503/429/timeout = Gemini ocupado: espera e tenta de novo
        time.sleep(wait)
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              params={"key": GEMINI_KEY},
                              json={"contents": [{"role": "user", "parts": prompt if isinstance(prompt, list)
                                                                           else [{"text": prompt}]}],
                                    "generationConfig": gen},
                              timeout=300)
        except requests.exceptions.Timeout:
            print(f"  Gemini {model}: demorou demais, tentando de novo", flush=True)
            continue
        if r.status_code == 400 and "thinking" in r.text.lower():
            gen.pop("thinkingConfig", None)
            continue
        if r.status_code not in (429, 500, 503) or wait == 90 or "quota" in r.text.lower():
            break  # sem cota não adianta esperar: vai pro próximo modelo
    if r is None:
        raise RuntimeError("timeout")
    if r.status_code != 200:
        if r.status_code == 429 and "quota" in r.text.lower():
            _GEMINI_DEAD.add(model)
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def ask_groq(model, content, json_mode=True):
    """Groq (API no formato OpenAI). content = texto ou lista de partes (texto + imagens)."""
    import time
    body = {"model": model, "temperature": 0.4, "max_completion_tokens": 8192,
            "messages": [{"role": "user", "content": content}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if "qwen" in model:
        body["reasoning_effort"] = "none"  # sem "pensar": resposta direta
    r = None
    for wait in (0, 20, 60):
        time.sleep(wait)
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                          json=body, timeout=180)
        if r.status_code == 400 and ("reasoning" in r.text or "response_format" in r.text):
            body.pop("reasoning_effort", None) if "reasoning" in r.text else body.pop("response_format", None)
            continue
        if r.status_code not in (429, 500, 503):
            break
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    txt = r.json()["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S)


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
    return ["BRASIL X EUA"], cuts


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
def pexels_search(q, pt=False):
    params = {"query": q, "orientation": "landscape", "per_page": 15}
    if pt:
        params["locale"] = "pt-BR"
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                     params=params, timeout=30)
    if r.status_code != 200:
        return []
    out = []
    for rank, v in enumerate(r.json().get("videos", [])):
        files = [f for f in v.get("video_files", []) if f.get("width") and f["width"] >= 1280
                 and f.get("file_type") == "video/mp4"]
        if not files or v.get("duration", 0) < 3:
            continue
        hd = [f for f in files if f["width"] >= 1920]
        f = min(hd, key=lambda f: f["width"]) if hd else max(files, key=lambda f: f["width"])
        out.append({"id": v["id"], "link": f["link"], "w": f["width"], "vdur": v.get("duration", 0),
                    "thumb": v.get("image", ""), "rank": rank, "q": q})
    return out


def broll_candidates(cut, per_cut=4):
    seen, cands = set(), []
    for qi, q in enumerate(cut.get("qs") or [cut["q"]]):
        for c in pexels_search(q, cut.get("pt", False)):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            c["score"] = qi * 10 + c["rank"] - (4 if c["w"] >= 1920 else 0)  # busca mais específica + Full HD/4K primeiro
            cands.append(c)
    cands.sort(key=lambda c: c["score"])
    return cands[:per_cut]


def vision_pick(cuts, out_words):
    """Gemini olha as miniaturas e escolhe o melhor b-roll de cada trecho (como um editor)."""
    import base64
    picks = {}
    if not (GEMINI_KEY or GROQ_KEY):
        return picks
    for start in range(0, len(cuts), 16):
        batch = list(enumerate(cuts))[start:start + 16]
        parts = [{"text": "Você é editor de Shorts virais. Para cada CORTE, veja a fala e escolha a imagem de "
                          "b-roll que melhor ilustra a frase, com melhor qualidade visual (nítida, bonita, cinematográfica, "
                          "sem texto/marca d'água, sem parecer genérica). "
                          'Responda SÓ JSON: {"picks": [{"corte": N, "opcao": M}, ...]}'}]
        for i, c in batch:
            if not c.get("cands"):
                continue
            fala = " ".join(w["word"] for w in out_words if c["t"] - 0.3 <= w["start"] < c["t"] + c["dur"])
            parts.append({"text": f"\nCORTE {i} — fala: \"{fala}\" — ideia: {c['q']}"})
            for j, cand in enumerate(c["cands"]):
                try:
                    url = cand["thumb"] + ("&" if "?" in cand["thumb"] else "?") + "auto=compress&cs=tinysrgb&w=360"
                    img = requests.get(url, timeout=20).content
                except Exception:
                    continue
                parts.append({"text": f"opção {j}:"})
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}})
        done = False
        for model in (dict.fromkeys(_GEMINI_OK + GEMINI_MODELS) if GEMINI_KEY else []):
            if model in _GEMINI_DEAD:
                continue
            try:
                txt = ask_gemini(model, parts)
                data = parse_json(txt)
                for p in data.get("picks", []):
                    picks[int(p["corte"])] = int(p["opcao"])
                done = True
                break
            except Exception as ex:
                print(f"  escolha visual {model} falhou ({str(ex)[:120]})", flush=True)
        if not done and GROQ_KEY:
            picks.update(vision_pick_groq(batch, out_words))
    return picks


def _contact_sheet(cands):
    """Junta as miniaturas de um corte numa imagem só (opções 0..3 numeradas) — a Groq aceita poucas imagens por pedido."""
    import io
    from PIL import ImageDraw, ImageFont
    cw, chh = 480, 270
    sheet = Image.new("RGB", (cw * 2, chh * 2), (0, 0, 0))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(os.path.join(os.path.dirname(__file__), "fonts", "Anton-Regular.ttf"), 54)
    except Exception:
        f = ImageFont.load_default()
    for j, cand in enumerate(cands[:4]):
        try:
            url = cand["thumb"] + ("&" if "?" in cand["thumb"] else "?") + "auto=compress&cs=tinysrgb&w=480"
            im = Image.open(io.BytesIO(requests.get(url, timeout=20).content)).convert("RGB")
        except Exception:
            continue
        sc = max(cw / im.width, chh / im.height)
        im = im.resize((max(cw, int(im.width * sc)), max(chh, int(im.height * sc))))
        im = im.crop(((im.width - cw) // 2, (im.height - chh) // 2, (im.width - cw) // 2 + cw, (im.height - chh) // 2 + chh))
        x, y = (j % 2) * cw, (j // 2) * chh
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + 62, y + 70], fill=(200, 16, 46))
        d.text((x + 16, y + 2), str(j), font=f, fill=(255, 255, 255))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def vision_pick_groq(batch, out_words):
    """Reserva: a Groq vê as miniaturas (uma imagem por corte, 4 opções numeradas) e escolhe."""
    import base64
    picks = {}
    items = [(i, c) for i, c in batch if c.get("cands")]
    for k in range(0, len(items), 5):  # até 5 imagens por pedido
        group = items[k:k + 5]
        content = [{"type": "text", "text":
                    "Você é editor de Shorts virais. Cada imagem mostra as opções de b-roll (0 a 3, número no canto) "
                    "de um CORTE. Para cada corte, escolha a opção que melhor ilustra a fala, com melhor qualidade "
                    "visual (nítida, bonita, cinematográfica, sem texto/marca d'água, sem parecer genérica). "
                    'Responda SÓ JSON: {"picks": [{"corte": N, "opcao": M}, ...]}'}]
        for i, c in group:
            fala = " ".join(w["word"] for w in out_words if c["t"] - 0.3 <= w["start"] < c["t"] + c["dur"])
            content.append({"type": "text", "text": f"CORTE {i} — fala: \"{fala}\" — ideia: {c['q']} — "
                                                    f"opções 0 a {len(c['cands'][:4]) - 1}:"})
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(_contact_sheet(c["cands"])).decode()}})
        for model in GROQ_VISION_MODELS:
            try:
                data = parse_json(ask_groq(model, content))
                for p in data.get("picks", []):
                    picks[int(p["corte"])] = int(p["opcao"])
                print(f"  escolha visual ok (groq {model})", flush=True)
                break
            except Exception as ex:
                print(f"  escolha visual groq {model} falhou ({str(ex)[:120]})", flush=True)
    return picks


def make_top_clip(cut, tmp, i, fake=False):
    outp = os.path.join(tmp, f"top{i:03d}.mp4")
    frames = max(1, round(cut["dur"] * FPS))
    if not fake and cut.get("link"):
        raw = os.path.join(tmp, f"raw{i:03d}.mp4")
        with requests.get(cut["link"], stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(raw, "wb") as f:
                for ch in r.iter_content(1 << 20):
                    f.write(ch)
        vdur = cut.get("vdur", 0)
        ss = min(max(0.5, vdur * 0.25), vdur - cut["dur"] - 0.3) if vdur > cut["dur"] + 1.5 else 0.3
        run(["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-stream_loop", "-1", "-i", raw, "-frames:v", str(frames), "-an",
             "-vf", f"scale={W}:{TOP_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{TOP_H},fps={FPS},"
                    # Ken Burns: aproximação lenta no b-roll
                    f"scale=w='2*trunc({W}*(1+0.08*t/{cut['dur']:.3f})/2)':h='2*trunc({TOP_H}*(1+0.08*t/{cut['dur']:.3f})/2)':eval=frame,"
                    f"crop={W}:{TOP_H}:'(in_w-{W})/2':'(in_h-{TOP_H})/2',setsar=1,eq=contrast=1.04:saturation=1.08",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", outp])
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


CAP_SIZE = 100
LIBASS_K = 0.644
SAFE_W = 900          # largura útil da legenda (90 px de margem de cada lado)
MAX_CAP_CHARS = 18    # grupos curtos: no máximo ~18 letras por vez
_CAP_FONT = None


def _cap_font(size):
    """Fonte real da legenda, pra medir a largura do texto."""
    global _CAP_FONT
    from PIL import ImageFont
    if _CAP_FONT is None:
        path = ""
        try:
            path = subprocess.run(["fc-match", "-f", "%{file}", FONT], capture_output=True, text=True).stdout.strip()
        except Exception:
            pass
        _CAP_FONT = path
    try:
        f = ImageFont.truetype(_CAP_FONT, size) if _CAP_FONT else None
        if f is not None:
            try:
                f.set_variation_by_name("ExtraBold")  # fonte variável: mede no peso certo
            except Exception:
                pass
        return f
    except Exception:
        return None


def fit_size(text, size):
    """Diminui a fonte até o texto (com contorno) caber em SAFE_W."""
    f = _cap_font(size)
    if f is not None:
        # o libass desenha a Montserrat ~0,644x da largura que o PIL mede (calibrado comparando os dois)
        w = (f.getlength(text) + len(text) * 1) * LIBASS_K + 2 * 7
    else:
        w = len(text) * size * 0.46
    if w <= SAFE_W:
        return size
    return max(56, int(size * SAFE_W / w))


# estilo da legenda: "yellow" (palavra falada em amarelo), "navy_box" ou "red_box" (caixinha atrás da palavra)
CAP_STYLE = os.getenv("CAP_STYLE") or "yellow"
CAP_BOX_COLORS = {"navy_box": "&H00602814&", "red_box": "&H002828D6&"}


def _txt_w(f, text, fs):
    if f is None:
        return len(text) * fs * 0.70
    return f.getlength(text) + len(text) * 1  # Spacing 1 do estilo


def word_box(texts, wi, fs):
    """Retângulo atrás da palavra wi (linha centralizada em x=540, y=640)."""
    f = _cap_font(fs)
    full = " ".join(texts)
    left = 540 - _txt_w(f, full, fs) / 2
    pre = " ".join(texts[:wi]) + (" " if wi else "")
    x0 = left + _txt_w(f, pre, fs)
    x1 = x0 + _txt_w(f, texts[wi], fs)
    pad = fs * 0.16
    return x0 - pad, x1 + pad, 640 - fs * 0.62, 640 + fs * 0.62


def rounded_rect(x0, y0, x1, y1, r):
    x0, y0, x1, y1, r = (int(round(v)) for v in (x0, y0, x1, y1, r))
    return (f"m {x0 + r} {y0} l {x1 - r} {y0} b {x1} {y0} {x1} {y0} {x1} {y0 + r} "
            f"l {x1} {y1 - r} b {x1} {y1} {x1} {y1} {x1 - r} {y1} "
            f"l {x0 + r} {y1} b {x0} {y1} {x0} {y1} {x0} {y1 - r} "
            f"l {x0} {y0 + r} b {x0} {y0} {x0} {y0} {x0 + r} {y0}")


def build_ass(out_words, title, total, credits, path):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},{CAP_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,2,5,30,30,0,1
Style: TitleR,{FONT},84,&H00FFFFFF,&H00FFFFFF,&H002828D6,&H002828D6,-1,0,0,0,100,100,1,0,3,20,0,5,30,30,0,1
Style: TitleB,{FONT},84,&H00FFFFFF,&H00FFFFFF,&H00A0461A,&H00A0461A,-1,0,0,0,100,100,1,0,3,20,0,5,30,30,0,1
Style: navy_box,{FONT},{CAP_SIZE},&HFF000000,&HFF000000,&H00602814,&HFF000000,-1,0,0,0,100,100,1,0,3,16,0,5,30,30,0,1
Style: red_box,{FONT},{CAP_SIZE},&HFF000000,&HFF000000,&H002828D6,&HFF000000,-1,0,0,0,100,100,1,0,3,16,0,5,30,30,0,1
Style: Box,{FONT},20,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Cred,{FONT},42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,28,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    title_end = min(TITLE_DUR, total) if (title and SHOW_TITLE) else 0.0
    if False:  # título antigo (tarjas em ASS) — substituído pelo papel rasgado em PNG
        # título: texto branco em tarjas vermelha e azul (cores dos EUA), na linha divisória, com "pop" de entrada
        lines = [l.replace("*", "").strip() for l in title if l.strip()]
        ys = [640] if len(lines) == 1 else [588, 702]
        for k, (line, y) in enumerate(zip(lines, ys)):
            d = k * 120  # segunda tarja entra logo depois
            ev.append(f"Dialogue: {3 + k},{ass_time(0)},{ass_time(title_end)},{'TitleR' if k == 0 else 'TitleB'},,0,0,0,,"
                      + r"{\pos(540," + str(y) + r")\fad(0,180)\fscx0\fscy0"
                      + rf"\t({d},{d + 160},\fscx108\fscy108)\t({d + 160},{d + 240},\fscx100\fscy100)" + "}" + line)
    # legendas: 1-3 palavras, só depois que o título sai
    words = [w for w in out_words if w["end"] > title_end]
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        chars = len(" ".join(clean(x["word"]) for x in cur))
        nxt_chars = chars + 1 + len(clean(nxt["word"])) if nxt else 0
        if (len(cur) >= 3 or nxt is None or nxt["start"] - w["end"] > 0.3 or nxt_chars > MAX_CAP_CHARS
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
            fs = fit_size(" ".join(texts), CAP_SIZE)
            if CAP_STYLE == "yellow":
                parts = [(r"{\c&H00D4FF&}" + t + r"{\c&HFFFFFF&}") if k == wi else t for k, t in enumerate(texts)]
            else:
                parts = texts
                # caixa só atrás da palavra falada: mesma linha num estilo de caixa, com as outras palavras invisíveis
                boxed = [(r"{\3a&H00&}" + t + r"{\3a&HFF&}") if k == wi else t for k, t in enumerate(texts)]
                ev.append(f"Dialogue: 0,{ass_time(st)},{ass_time(en)},{CAP_STYLE},,0,0,0,,"
                          + r"{\pos(540,640)\fs" + str(fs) + r"\3a&HFF&}" + " ".join(boxed))
            ev.append(f"Dialogue: 1,{ass_time(st)},{ass_time(en)},Cap,,0,0,0,,"
                      + r"{\pos(540,640)\fs" + str(fs) + "}" + " ".join(parts))
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
        # "jump cuts": quebra cada trecho em planos de ~2,5-3,5 s (nas pausas entre palavras) e alterna o zoom
        shots = make_shots(segs, words)
        print(f"Planos (jump cuts): {len(shots)}", flush=True)
        parts = []
        for i, sh in enumerate(shots):
            d = sh["end"] - sh["start"]
            cw, ch, x, y = crop_for(faces, sh["start"], sh["end"], W, H)
            lvl = ZOOM_LEVELS[i % len(ZOOM_LEVELS)]
            # zoom: nível do plano + "soco" rápido no começo + aproximação lenta contínua (a imagem nunca fica parada)
            z = f"({lvl}+{PUSH}*t/{d:.3f}+{PUNCH}*max(0,1-t/0.22))"
            p = os.path.join(tmp, f"bot{i:03d}.mp4")
            run(["ffmpeg", "-y", "-ss", f"{sh['start']:.3f}", "-i", norm, "-t", f"{d:.3f}", "-an",
                 "-vf", f"crop={cw}:{ch}:{x}:{y},scale={W}:{BOT_H},"
                        f"scale=w='2*trunc({W}*{z}/2)':h='2*trunc({BOT_H}*{z}/2)':eval=frame:flags=bicubic,"
                        f"crop={W}:{BOT_H}:'(in_w-{W})/2':'(in_h-{BOT_H})*0.35',setsar=1",
                 "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", p])
            parts.append(p)
        bottom_v = os.path.join(tmp, "bottom_v.mp4")
        with open(os.path.join(tmp, "b.txt"), "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(tmp, "b.txt"), "-c", "copy", bottom_v])
        # áudio contínuo (cortado só nas pausas), num passo só: sem estalos entre os planos
        af = ";".join(f"[0:a]atrim={sg['src_start']:.3f}:{sg['src_end']:.3f},asetpts=PTS-STARTPTS,"
                      f"afade=t=in:d=0.015,afade=t=out:st={max(0, sg['src_end'] - sg['src_start'] - 0.015):.3f}:d=0.015[a{k}]"
                      for k, sg in enumerate(segs))
        af += ";" + "".join(f"[a{k}]" for k in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[aout]"
        bottom = os.path.join(tmp, "bottom.mp4")
        run(["ffmpeg", "-y", "-i", norm, "-i", bottom_v, "-filter_complex", af, "-map", "1:v", "-map", "[aout]",
             "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", "-shortest", bottom])
        total = probe_duration(bottom)

        # plano: título + b-rolls
        title, cuts, plan_src = None, None, "ia"
        if GEMINI_KEY or GROQ_KEY:
            try:
                title, cuts = llm_plan(out_words, total)
            except Exception as ex:
                print(f"IA falhou ({ex}); usando plano simples", flush=True)
        if not cuts:
            if os.getenv("ALLOW_SIMPLE_PLAN") != "1":
                # sem IA o b-roll fica ruim: melhor adiar e tentar de novo na próxima execução
                raise RuntimeError("IA indisponível agora (Gemini e Groq ocupados ou sem cota) — vídeo adiado")
            title, cuts = fallback_plan(out_words, total)
            plan_src = "simples"
        cuts = normalize_cuts(cuts, total)
        print(f"Título: {' / '.join(title)}", flush=True)

        # topo: b-rolls — candidatos do Pexels + escolha visual pelo Gemini
        fake = fake_broll or not PEXELS_KEY
        if not fake:
            for c in cuts:
                try:
                    c["cands"] = broll_candidates(c)
                except Exception as ex:
                    print(f"  busca '{c['q']}' falhou: {ex}", flush=True)
                    c["cands"] = []
            picks = vision_pick(cuts, out_words)
            print(f"Escolha visual: {len(picks)}/{len(cuts)} trechos", flush=True)
            used = set()
            for i, c in enumerate(cuts):
                order = list(range(len(c["cands"])))
                if i in picks and 0 <= picks[i] < len(order):
                    order.remove(picks[i])
                    order.insert(0, picks[i])
                for j in order:
                    cand = c["cands"][j]
                    if cand["id"] not in used:
                        used.add(cand["id"])
                        c.update(link=cand["link"], vdur=cand["vdur"], chosen=cand["q"], w=cand["w"])
                        break
        tops = []
        for i, c in enumerate(cuts):
            try:
                tops.append(make_top_clip(c, tmp, i, fake=fake))
            except Exception as ex:
                print(f"  b-roll '{c['q']}' falhou: {ex}", flush=True)
                tops.append(make_top_clip(c, tmp, i, fake=True))
            print(f"  {c['t']:5.1f}s  {c.get('chosen', c['q'])}  ({c.get('w', '-')}px)", flush=True)
        top = os.path.join(tmp, "top.mp4")
        with open(os.path.join(tmp, "t.txt"), "w") as f:
            f.writelines(f"file '{p}'\n" for p in tops)
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(tmp, "t.txt"), "-c", "copy", top])

        ass = os.path.join(tmp, "caps.ass")
        build_ass(out_words, title, total, ["Vídeos: Pexels"], ass)
        ass_esc = ass.replace("\\", "/").replace(":", "\\:")
        sub = f"subtitles='{ass_esc}'" + (f":fontsdir='{FONTS_DIR}'" if FONTS_DIR else "")
        inputs, fc = ["-i", bottom, "-i", top], f"[1:v]tpad=stop_mode=clone:stop_duration=5[t];[t][0:v]vstack=inputs=2:shortest=1,{sub}[base]"
        if title and SHOW_TITLE:
            from editor.title_card import render_paper_title
            tpng = os.path.join(tmp, "title.png")
            ttext = " ".join(l.replace("*", "").strip() for l in title if l.strip())
            timg = render_paper_title(ttext, scale=1.0)
            if timg.width > 980:  # título longo: reduz pra caber com margem
                timg = timg.resize((980, int(timg.height * 980 / timg.width)), Image.LANCZOS)
            timg.save(tpng)
            tw_, th_ = timg.size
            te = min(TITLE_DUR, total)
            inputs += ["-loop", "1", "-t", f"{te:.2f}", "-i", tpng]
            # papel entra com um "pop" (cresce rápido) e some com fade no fim
            fc += (f";[2:v]format=rgba,scale=w='trunc(iw*min(1,0.6+t*4)/2)*2':h=-2:eval=frame,"
                   f"fade=t=out:st={te - 0.2:.2f}:d=0.2:alpha=1,setpts=PTS-STARTPTS[ti]"
                   f";[base][ti]overlay=x='(W-w)/2':y='{TOP_H}-h/2':eof_action=pass:enable='lt(t,{te:.2f})'[ov]")
            last = "ov"
        else:
            last = "base"
        fc += f";[{last}]format=yuv420p[v]"
        run(["ffmpeg", "-y", *inputs, "-filter_complex", fc,
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
