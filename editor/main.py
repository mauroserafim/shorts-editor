"""
Robô: procura vídeos novos na pasta do Drive, edita e sobe o Short pronto em "Prontos".

Drive via rclone (remote "gdrive:" configurado por variáveis de ambiente no workflow).
    python -m editor.main --check   -> só diz se tem vídeo pendente (para o GitHub pular o resto)
    python -m editor.main           -> processa os pendentes
"""
import json
import os
import subprocess
import sys

REMOTE = "gdrive:"
OUT_DIR = "Prontos"
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "6"))
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".3gp")


def rclone(*args):
    r = subprocess.run(["rclone", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise RuntimeError("rclone " + " ".join(args[:2]) + " falhou")
    return r.stdout


def lsjson(path):
    try:
        return json.loads(rclone("lsjson", path, "--files-only", "--max-depth", "1") or "[]")
    except RuntimeError:
        return []


def out_name(name):
    return os.path.splitext(name)[0] + " - SHORT.mp4"


def pending():
    vids = [f for f in lsjson(REMOTE)
            if f.get("MimeType", "").startswith("video/") or f["Name"].lower().endswith(VIDEO_EXT)]
    done = {f["Name"] for f in lsjson(REMOTE + OUT_DIR)}
    todo = [f for f in vids if out_name(f["Name"]) not in done]
    todo.sort(key=lambda f: f.get("ModTime", ""))
    return todo


def main():
    todo = pending()
    if "--check" in sys.argv:
        print(f"Pendentes: {[f['Name'] for f in todo]}")
        gh = os.getenv("GITHUB_OUTPUT")
        if gh:
            with open(gh, "a") as f:
                f.write(f"pending={'true' if todo else 'false'}\n")
        return

    from editor.edit import edit  # só aqui: no --check as libs ainda não foram instaladas

    os.makedirs("work", exist_ok=True)
    report = ["# Shorts editados\n"]
    for f in todo[:MAX_PER_RUN]:
        name = f["Name"]
        print(f"\n=== {name} ===", flush=True)
        src = os.path.join("work", "in" + os.path.splitext(name)[1])
        out = os.path.join("work", "out.mp4")
        rclone("copyto", REMOTE + name, src)
        try:
            s = edit(src, out)
        except Exception as ex:
            print(f"ERRO em {name}: {ex}", flush=True)
            report.append(f"- ❌ **{name}**: {ex}")
            if "travou" in str(ex):
                break  # ffmpeg travou: para aqui (os outros travariam igual) e deixa o erro no log
            continue
        rclone("copyto", out, f"{REMOTE}{OUT_DIR}/{out_name(name)}")
        line = (f"- ✅ **{out_name(name)}** — {s['duracao_original']}s → {s['duracao_final']}s · "
                f"{len(s['brolls'])} b-rolls · título: {' / '.join(s['titulo']).replace('*', '')} · plano: {s['plano']}")
        print(line, flush=True)
        report.append(line)
        for p in (src, out):
            if os.path.exists(p):
                os.remove(p)

    summ = os.getenv("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as fh:
            fh.write("\n".join(report) + "\n")


if __name__ == "__main__":
    main()
