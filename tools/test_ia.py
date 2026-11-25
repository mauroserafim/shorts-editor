import base64, os, requests
T = os.environ["GITHUB_TOKEN"]
img = base64.b64encode(requests.get("https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg?auto=compress&cs=tinysrgb&w=360", timeout=30).content).decode()
urls = ["https://models.github.ai/inference/chat/completions", "https://models.inference.ai.azure.com/chat/completions"]
hdrs = {"gh": {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, "json": {"Accept": "application/json"}, "none": {}}
for url in urls:
    for hn, h in hdrs.items():
        for model in ("openai/gpt-4.1-mini", "gpt-4.1-mini"):
            for kind in ("text", "image"):
                content = "Responda só JSON {\"ok\": true}" if kind == "text" else [
                    {"type": "text", "text": "Que animal é? Responda só JSON {\"animal\": \"...\"}"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img, "detail": "low"}}]
                try:
                    r = requests.post(url, headers={"Authorization": f"Bearer {T}", "Content-Type": "application/json", **h},
                                      json={"model": model, "messages": [{"role": "user", "content": content}]}, timeout=60)
                    body = r.text[:160].replace("\n", " ")
                    print(f"{url.split('/')[2]:32} {hn:5} {model:20} {kind:5} -> {r.status_code} {r.headers.get('content-type','')[:30]} | {body}")
                except Exception as ex:
                    print(f"{url} {hn} {model} {kind} EXC {ex}")
