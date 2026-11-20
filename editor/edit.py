"""
Editor automático de Shorts (grátis): Whisper + ffmpeg + Pexels.

Uso:
    python -m editor.edit entrada.mp4 saida.mp4
Opções de teste:
    --words-json palavras.json   (pula o Whisper)
    --fake-broll                 (gera b-roll de teste, sem Pexels)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from difflib import SequenceMatcher

import requests

W, H, FPS = 1080, 1920, 30

# ---------------------------------------------------------------- config
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
MAX_GAP = float(os.getenv("MAX_GAP", "0.30"))        # pausa máxima mantida (s)
PAD_START, PAD_END = 0.06, 0.10
BROLL_EVERY = float(os.getenv("BROLL_EVERY", "6.0"))  # 1 b-roll a cada N s
BROLL_LEN = float(os.getenv("BROLL_LEN", "2.8"))
ZOOM = float(os.getenv("ZOOM", "1.15"))
FONT = os.getenv("CAPTION_FONT", "Montserrat ExtraBold")
FONTS_DIR = os.getenv("FONTS_DIR", "")

ALWAYS_FILLERS = {"ah", "ahn", "eh", "éh", "éé", "ééé", "hum", "hmm", "hm", "uhm", "uh", "né", "ne"}
SOFT_FILLERS = {"é", "tipo", "então", "assim", "aí"}  # só quando isolados por vírgula/pausa

STOP = set("""a o as os um uma uns umas de da do das dos em no na nos nas num numa por pra para pro
com sem que se e é ou mas mais muito muita muitos muitas já não sim eu tu ele ela eles elas nós
você vocês me te lhe nos meu minha seu sua isso isto aquilo esse essa este esta aqui ali lá aí
então tipo né também como quando onde porque porquê ser estar ter tem têm tinha tinham foi fui
era são está estão vai vou ia faz fazer cada mesmo coisa gente cara vez vezes ver vi bem mal até
sobre entre conforme todo todos toda todas outro outra ponto vista lado lados senão às ao aos
pelo pela pelos pelas dele dela deles delas seus suas lugar lugares etc sempre nunca consegue
conseguem conseguir falar falaram observar perspectiva exemplo trabalhei trabalho trabalha
""".split())


def norm(w):
    w = w.strip().lower()
    w = re.sub(r"[^\wà-ü-]", "", w)
    return w


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise RuntimeError("falhou: " + " ".join(cmd[:6]))
    return r.stdout


def probe_duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nk=1:nw=1", path]).strip())


# ---------------------------------------------------------------- 1. transcrição
def transcribe(path):
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(path, language="pt", word_timestamps=True,
                               vad_filter=True, condition_on_previous_text=False,
                               initial_prompt="Vídeo em português do Brasil, falando sobre a vida nos Estados Unidos.")
    words = []
    for s in segs:
        for w in s.words or []:
            if w.word.strip():
                words.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return words


# ---------------------------------------------------------------- 2. o que cortar
def mark_cuts(words):
    n = len(words)
    keep = [True] * n
    toks = [norm(w["word"]) for w in words]

    # 2a. vícios de linguagem
    for i, w in enumerate(words):
        t = toks[i]
        if not t:
            continue
        if t in ALWAYS_FILLERS or w["word"].rstrip().endswith(("--", "—")):  # palavra cortada no meio
            keep[i] = False
        elif t in SOFT_FILLERS:
            gap_before = w["start"] - words[i - 1]["end"] if i > 0 else 1
            gap_after = words[i + 1]["start"] - w["end"] if i + 1 < n else 1
            prev_comma = i > 0 and words[i - 1]["word"].rstrip().endswith(",")
            has_comma = w["word"].rstrip().endswith((",", "…", "..."))
            if (has_comma and (prev_comma or gap_before > 0.25)) or (gap_before > 0.35 and gap_after > 0.35):
                keep[i] = False

    # 2b. repetições imediatas ("num, num", "sou engenheiro, sou engenheiro") -> fica a última
    changed = True
    while changed:
        changed = False
        idx = [i for i in range(n) if keep[i] and toks[i]]
        for size in range(6, 0, -1):
            for k in range(len(idx) - 2 * size + 1):
                a = [toks[j] for j in idx[k:k + size]]
                b = [toks[j] for j in idx[k + size:k + 2 * size]]
                if a == b:
                    for j in idx[k:k + size]:
                        keep[j] = False
                    changed = True
                    break
            if changed:
                break

    # 2c. frases regravadas (falou, parou, começou de novo) -> fica a última tomada
    phrases, cur = [], []
    idx = [i for i in range(n) if keep[i] and toks[i]]
    for p, i in enumerate(idx):
        cur.append(i)
        nxt = idx[p + 1] if p + 1 < len(idx) else None
        end_sent = words[i]["word"].rstrip().endswith((".", "?", "!", "…"))
        big_gap = nxt is not None and words[nxt]["start"] - words[i]["end"] > 0.6
        if nxt is None or end_sent or big_gap:
            phrases.append(cur)
            cur = []
    for a in range(len(phrases)):
        pa = [toks[i] for i in phrases[a]]
        for b in range(a + 1, min(a + 4, len(phrases))):
            pb = [toks[i] for i in phrases[b]]
            if len(pa) < 2 or len(pb) < 2 or pa[:2] != pb[:2]:
                continue
            ratio = SequenceMatcher(None, pa, pb[:len(pa)]).ratio()
            if len(pa) <= 3 or ratio >= 0.6:
                for i in phrases[a]:
                    keep[i] = False
                break
    return keep


def build_segments(words, keep, duration):
    kept = [w for w, k in zip(words, keep) if k]
    segs = []
    for w in kept:
        s, e = max(0.0, w["start"] - PAD_START), min(duration, w["end"] + PAD_END)
        if segs and s - segs[-1][1] <= MAX_GAP:
            segs[-1][1] = max(segs[-1][1], e)
        else:
            if segs and s < segs[-1][1]:
                s = segs[-1][1]
            segs.append([s, e])
    segs = [x for x in segs if x[1] - x[0] >= 0.15]
    out, t = [], 0.0
    for s, e in segs:
        out.append({"src_start": s, "src_end": e, "out_start": t})
        t += e - s
    return out, t


def map_time(t, segs):
    for s in segs:
        if s["src_start"] - 1e-3 <= t <= s["src_end"] + 1e-3:
            return s["out_start"] + min(max(t, s["src_start"]), s["src_end"]) - s["src_start"]
    return None


# ---------------------------------------------------------------- 3. render do corte com zoom
def render_cut(src, segs, out, tmp):
    norm_path = os.path.join(tmp, "norm.mp4")
    run(["ffmpeg", "-y", "-i", src, "-vf",
         f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", norm_path])

    # zoom alterna em blocos de ~3 s (estilo jump-cut / punch-in)
    zooms, acc, z = [], 0.0, 1.0
    for s in segs:
        zooms.append(z)
        acc += s["src_end"] - s["src_start"]
        if acc >= 3.0:
            z = ZOOM if z == 1.0 else 1.0
            acc = 0.0

    parts = []
    for i, (s, z) in enumerate(zip(segs, zooms)):
        d = s["src_end"] - s["src_start"]
        p = os.path.join(tmp, f"seg{i:03d}.mp4")
        vf = f"crop=iw/{z}:ih/{z}:(iw-iw/{z})/2:(ih-ih/{z})/2.6,scale={W}:{H},setsar=1" if z != 1.0 else "setsar=1"
        fade = min(0.02, d / 4)
        run(["ffmpeg", "-y", "-ss", f"{s['src_start']:.3f}", "-i", norm_path, "-t", f"{d:.3f}",
             "-vf", vf, "-af", f"afade=t=in:d={fade},afade=t=out:st={d - fade:.3f}:d={fade}",
             "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", p])
        parts.append(p)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return zooms


# ---------------------------------------------------------------- 4. legendas animadas (ASS)
def ass_time(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean_caption(w):
    w = w.strip().strip("\"“”'«»")
    w = re.sub(r"[,.;:…]+$", "", w)
    w = w.replace("--", "")
    return w.upper()


def build_ass(out_words, path):
    groups, cur = [], []
    for i, w in enumerate(out_words):
        cur.append(w)
        nxt = out_words[i + 1] if i + 1 < len(out_words) else None
        brk = (len(cur) >= 3 or nxt is None or nxt["start"] - w["end"] > 0.35
               or w["word"].rstrip().endswith((".", "?", "!", ",", "…")))
        if brk:
            groups.append(cur)
            cur = []

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for gi, g in enumerate(groups):
        g_end_next = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.4
        texts = [clean_caption(w["word"]) for w in g]
        for wi, w in enumerate(g):
            st = w["start"]
            en = g[wi + 1]["start"] if wi + 1 < len(g) else min(g_end_next, g[-1]["end"] + 0.4)
            if en <= st:
                continue
            parts = []
            for k, t in enumerate(texts):
                if k == wi:
                    parts.append(r"{\c&H00E5FF&\fscx112\fscy112}" + t + r"{\r}")
                else:
                    parts.append(t)
            pop = r"{\fad(0,0)}" if wi else r"{\t(0,80,\fscx108\fscy108)\t(80,160,\fscx100\fscy100)}"
            lines.append(f"Dialogue: 0,{ass_time(st)},{ass_time(en)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return len(groups)


# ---------------------------------------------------------------- 5. b-roll (Pexels)
def broll_query(text_words):
    cands = []
    for w in text_words:
        t = norm(w)
        verbish = re.search(r"(ava|avam|ar|er|ir|ando|endo|indo|ou|aram|eram|iram|emos|amos|ia|iam)$", t)
        if len(t) >= 4 and t not in STOP and not t.isdigit() and not verbish:
            cands.append(t)
    # mantém ordem, tira repetidos, prioriza palavras mais longas
    seen, uniq = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    uniq.sort(key=len, reverse=True)
    return " ".join(uniq[:2])


def pexels_search(query, used):
    r = requests.get("https://api.pexels.com/videos/search",
                     headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "orientation": "portrait", "per_page": 15,
                             "locale": "pt-BR", "size": "medium"}, timeout=30)
    if r.status_code != 200:
        return None
    for v in r.json().get("videos", []):
        if v["id"] in used or v.get("duration", 0) < BROLL_LEN + 0.3:
            continue
        files = [f for f in v.get("video_files", [])
                 if f.get("height") and f.get("width") and f["height"] > f["width"] and f["height"] >= 1280
                 and f.get("file_type") == "video/mp4"]
        if not files:
            continue
        files.sort(key=lambda f: abs(f["height"] - 1920))
        used.add(v["id"])
        return files[0]["link"]
    return None


def fetch_broll(query, used, tmp, i, fake=False):
    raw = os.path.join(tmp, f"broll_raw{i}.mp4")
    outp = os.path.join(tmp, f"broll{i}.mp4")
    if fake:
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d={BROLL_LEN}",
             "-vf", f"drawtext=text='{query}':fontsize=70:fontcolor=white:x=(w-tw)/2:y=200:box=1:boxcolor=black",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", outp])
        return outp
    link = None
    for q in (query, query.split(" ")[0] if query else ""):
        if q:
            link = pexels_search(q, used)
            if link:
                break
    if not link:
        return None
    with requests.get(link, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(raw, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    run(["ffmpeg", "-y", "-ss", "0.3", "-i", raw, "-t", f"{BROLL_LEN}", "-an",
         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", outp])
    return outp


def plan_brolls(out_words, total):
    slots, t = [], 2.5
    while t + BROLL_LEN < total - 3.0:
        # começa no início da próxima palavra depois de t
        w0 = next((w for w in out_words if w["start"] >= t), None)
        if not w0 or w0["start"] + BROLL_LEN > total - 3.0:
            break
        st = w0["start"]
        words_in = [w["word"] for w in out_words if st - 0.5 <= w["start"] <= st + BROLL_LEN + 1.0]
        slots.append({"start": st, "query": broll_query(words_in)})
        t = st + BROLL_EVERY
    return slots


# ---------------------------------------------------------------- 6. montagem final
def final_render(cut, brolls, ass, out):
    inputs = ["-i", cut]
    fc, last = [], "0:v"
    for k, b in enumerate(brolls, start=1):
        inputs += ["-i", b["file"]]
        st = b["start"]
        fc.append(f"[{k}:v]setpts=PTS-STARTPTS+{st:.3f}/TB,"
                  f"fade=t=in:st={st:.3f}:d=0.12,fade=t=out:st={st + BROLL_LEN - 0.12:.3f}:d=0.12:alpha=1,"
                  f"format=yuva420p[b{k}]")
        fc.append(f"[{last}][b{k}]overlay=0:0:eof_action=pass:enable='between(t,{st:.3f},{st + BROLL_LEN:.3f})'[v{k}]")
        last = f"v{k}"
    ass_esc = ass.replace("\\", "/").replace(":", "\\:")
    sub = f"subtitles='{ass_esc}'" + (f":fontsdir='{FONTS_DIR}'" if FONTS_DIR else "")
    fc.append(f"[{last}]{sub},format=yuv420p[vout]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])


# ---------------------------------------------------------------- main
def edit(src, out, words_json=None, fake_broll=False):
    tmp = tempfile.mkdtemp(prefix="short_")
    try:
        duration = probe_duration(src)
        if words_json:
            words = json.load(open(words_json, encoding="utf-8"))
        else:
            print("Transcrevendo (Whisper)…", flush=True)
            words = transcribe(src)
        if not words:
            raise RuntimeError("Nenhuma fala encontrada no vídeo.")

        keep = mark_cuts(words)
        segs, total = build_segments(words, keep, duration)
        removed = [w["word"] for w, k in zip(words, keep) if not k]
        print(f"Cortes: {len(segs)} trechos, {duration:.1f}s -> {total:.1f}s", flush=True)

        out_words = []
        for w, k in zip(words, keep):
            if not k:
                continue
            s, e = map_time(w["start"], segs), map_time(w["end"], segs)
            if s is None or e is None:
                continue
            out_words.append({"word": w["word"], "start": s, "end": max(e, s + 0.05)})

        cut = os.path.join(tmp, "cut.mp4")
        render_cut(src, segs, cut, tmp)

        ass = os.path.join(tmp, "caps.ass")
        build_ass(out_words, ass)

        brolls, used = [], set()
        if PEXELS_KEY or fake_broll:
            for i, slot in enumerate(plan_brolls(out_words, total)):
                if not slot["query"]:
                    continue
                try:
                    f = fetch_broll(slot["query"], used, tmp, i, fake=fake_broll)
                except Exception as ex:  # b-roll nunca derruba a edição
                    print(f"  b-roll '{slot['query']}' falhou: {ex}", flush=True)
                    f = None
                if f:
                    brolls.append({**slot, "file": f})
                    print(f"  b-roll {slot['start']:.1f}s: {slot['query']}", flush=True)
        else:
            print("Sem PEXELS_API_KEY: sem b-roll.", flush=True)

        final_render(cut, brolls, ass, out)
        summary = {
            "duracao_original": round(duration, 1),
            "duracao_final": round(total, 1),
            "trechos": len(segs),
            "brolls": [{"t": round(b["start"], 1), "busca": b["query"]} for b in brolls],
            "palavras_cortadas": removed,
        }
        return summary
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
