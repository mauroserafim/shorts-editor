#!/bin/bash
# Reproduz a montagem final (vstack + legenda + título) com vídeos de teste, para achar travamentos do ffmpeg
set -x
ffmpeg -version | head -1
cd "$(mktemp -d)"
ffmpeg -loglevel error -y -f lavfi -i testsrc2=s=1080x1280:r=30 -f lavfi -i sine=f=440:sample_rate=48000 -t 20 -c:v libx264 -preset veryfast -c:a aac -shortest bottom.mp4
ffmpeg -loglevel error -y -f lavfi -i testsrc2=s=1080x640:r=30 -t 20 -c:v libx264 -preset veryfast top.mp4
python -c "
import sys; sys.path.insert(0,'$GITHUB_WORKSPACE')
from editor.title_card import render_paper_title
render_paper_title('BRASIL X EUA O CHOQUE', scale=1.0).save('title.png')"
printf '[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: D,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,10,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:04.00,0:00:08.00,D,,0,0,0,,teste\n' > caps.ass
BASE="[1:v]tpad=stop_mode=clone:stop_duration=5[t];[t][0:v]vstack=inputs=2:shortest=1,subtitles='caps.ass'[base]"
ENC="-map [v] -map 0:a -c:v libx264 -preset medium -crf 19 -r 30 -c:a aac -b:a 192k -shortest -movflags +faststart"
echo "=== A: atual (loop + scale por frame)"
time timeout 120 ffmpeg -loglevel error -y -i bottom.mp4 -i top.mp4 -loop 1 -t 3.00 -i title.png -filter_complex "$BASE;[2:v]format=rgba,scale=w='trunc(iw*min(1,0.6+t*4)/2)*2':h=-2:eval=frame,fade=t=out:st=2.80:d=0.2:alpha=1,setpts=PTS-STARTPTS[ti];[base][ti]overlay=x='(W-w)/2':y='640-h/2':eof_action=pass:enable='lt(t,3.00)'[ov];[ov]format=yuv420p[v]" $ENC a.mp4; echo "A exit=$?"
echo "=== B: sem título"
time timeout 120 ffmpeg -loglevel error -y -i bottom.mp4 -i top.mp4 -filter_complex "$BASE;[base]format=yuv420p[v]" $ENC b.mp4; echo "B exit=$?"
echo "=== C: título em sequência de PNGs (sem loop)"
python -c "
from PIL import Image
im=Image.open('title.png')
for i in range(90):
    t=i/30; s=min(1,0.6+t*4); a=1 if t<2.8 else max(0,(3-t)/0.2)
    w=max(2,int(im.width*s)//2*2); h=max(2,int(im.height*s)//2*2)
    fr=im.resize((w,h)); 
    if a<1: fr.putalpha(fr.getchannel('A').point(lambda v:int(v*a)))
    c=Image.new('RGBA',im.size,(0,0,0,0)); c.paste(fr,((im.width-w)//2,(im.height-h)//2)); c.save(f'tt_{i:03d}.png')"
time timeout 120 ffmpeg -loglevel error -y -i bottom.mp4 -i top.mp4 -framerate 30 -i tt_%03d.png -filter_complex "$BASE;[base][2:v]overlay=x='(W-w)/2':y='640-h/2':eof_action=pass[ov];[ov]format=yuv420p[v]" $ENC c.mp4; echo "C exit=$?"
ls -la *.mp4
