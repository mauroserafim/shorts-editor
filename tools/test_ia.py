"""Testa a reserva Groq (texto + visão) usando o mesmo código do editor."""
import os
import requests
import editor.edit as e

if not e.GROQ_KEY:
    raise SystemExit("Falta o secret GROQ_API_KEY")
ms = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {e.GROQ_KEY}"}, timeout=30).json()
print("Modelos:", sorted(m["id"] for m in ms.get("data", [])))
print("Texto:", e.groq_models("text"), "Visão:", e.groq_models("vision"))
for m in e.groq_models("text"):
    try:
        print("TEXTO", m, "->", e.parse_json(e.ask_groq(m, 'Responda só JSON {"title": ["GASOLINA NOS EUA"]}')))
    except Exception as ex:
        print("TEXTO", m, "FALHOU", str(ex)[:200])
cands = [{"thumb": f"https://images.pexels.com/photos/{i}/pexels-photo-{i}.jpeg"} for i in (1108099, 210019, 466685, 1640777)]
cut = {"t": 0.0, "dur": 3.0, "q": "dog playing in park", "cands": cands}
words = [{"word": w, "start": 0.5} for w in "meu cachorro adora brincar no parque".split()]
print("VISÃO ->", e.vision_pick_groq([(0, cut)], words), "(esperado: opção 0 = cachorro)")
