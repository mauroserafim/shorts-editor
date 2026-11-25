#!/bin/bash
# Edição completa com vídeo/fala de teste (sem Drive, Whisper, Pexels ou IA) para medir cada passo no ffmpeg do GitHub
set -e
ffmpeg -version | head -1
D=$(mktemp -d)
ffmpeg -loglevel error -y -f lavfi -i "testsrc2=s=1080x1920:r=30" -f lavfi -i "sine=f=300:sample_rate=48000" -t ${DUR:-90} -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -shortest $D/in.mp4
python - "$D" <<'PY'
import json, sys
w=[]; t=0.3; i=0
txt='olá pessoal hoje eu vou mostrar como é a vida aqui nos estados unidos comparado com o brasil e é muito diferente'.split()
while t < float(__import__("os").getenv("DUR","90")) - 2:
    w.append({'word':txt[i%len(txt)],'start':round(t,2),'end':round(t+0.35,2)}); t += 0.45 if i%9 else 1.2; i+=1
json.dump(w, open(sys.argv[1]+'/w.json','w'))
PY
time ALLOW_SIMPLE_PLAN=1 FFMPEG_TIMEOUT=600 python -m editor.edit $D/in.mp4 $D/out.mp4 --words-json $D/w.json --fake-broll | tail -3
ls -la $D/out.mp4
