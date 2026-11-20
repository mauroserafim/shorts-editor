# Editor automático de Shorts (grátis)

Joga o vídeo bruto na pasta **Claude** do Google Drive → em até ~15 min o robô do GitHub Actions
edita e salva em **Claude/Prontos** como `<nome> - SHORT.mp4`.

O que ele faz:
- Transcreve com Whisper (local, grátis)
- Corta silêncios, vícios ("né", "é", "ahn"), repetições ("num, num") e frases regravadas (fica a última tomada)
- B-roll de stock do Pexels a cada ~6 s (não cobre os 2,5 s iniciais nem os 3 s finais)
- Legendas animadas 3 palavras por vez, palavra falada em amarelo
- Zoom punch-in alternado (jump-cut)

## Configuração (uma vez só)

### 1. Secrets do repositório
Settings → Secrets and variables → Actions → **New repository secret**

| Nome | Valor |
|---|---|
| `PEXELS_API_KEY` | sua chave do Pexels |
| `RCLONE_TOKEN` | o JSON do passo 2 |

### 2. Token do Google Drive (no PC, ~2 min)
1. Baixe o rclone: https://rclone.org/downloads/ (Windows, zip) e extraia.
2. No PowerShell, dentro da pasta extraída:
   ```
   .\rclone.exe authorize "drive"
   ```
3. O navegador abre → entre com `mauroserafim1@gmail.com` → Permitir.
4. O terminal mostra um bloco entre `--->` e `<---End paste`. Copie **só o JSON** `{"access_token": ... }` e cole no secret `RCLONE_TOKEN`.

### 3. Pronto
- Roda sozinho a cada 15 min (o GitHub às vezes atrasa um pouco).
- Para rodar na hora: aba **Actions** → *Editar Shorts* → **Run workflow**.

## Ajustes (Settings → Variables, opcional)
| Variável | Padrão | O que faz |
|---|---|---|
| `WHISPER_MODEL` | `medium` | `small` = mais rápido, `large-v3` = mais preciso |
| `DRIVE_FOLDER_ID` | pasta Claude | outra pasta de entrada |

Outros no `editor/edit.py`: `BROLL_EVERY`, `BROLL_LEN`, `ZOOM`, `MAX_GAP`.

## Rodar no PC
```
pip install -r requirements.txt   # + ffmpeg no PATH
set PEXELS_API_KEY=...
python -m editor.edit bruto.mp4 short.mp4
```
