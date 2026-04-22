**service: portfolio-ai**

runs on picxibox (server pc), 24/7, port `8765`

---

**endpoint**

```
GET /summary
```

```json
{
  "total": 8273.84,
  "invested": 6105.91,
  "unrealised_pnl": 864.56,
  "unrealised_pct": 14.15,
  "cash": 1303.37,
  "positions": 19,
  "ai_summary": "...",
  "inference_available": true
}
```

---

**flow**

```
GET /summary
  → hit t212 api (cached 5 mins)
  → try GET http://inference.picxi.uk/api/tags
      reachable → POST prompt to /api/generate
      not reachable → ai_summary: null, inference_available: false
  → return everything
```

---

**ollama prompt**

```
you are a terse portfolio assistant. 2 sentences max.
cover overall health, biggest mover, one thing worth watching.
no fluff, numbers where useful, be direct.

positions: {positions_json}
balance: {balance_json}
```

model: `llama3.1:8b`

---

**files**

```
/opt/picxibox/portfolio-ai/
  docker-compose.yml
  app/
    main.py         ← fastapi app
    t212_api.py     ← copy of existing client
    requirements.txt
```

runs as a container, joins npm network, proxy host at `portfolio.picxi.uk`

---

**conky replacement**

all 5 execi calls become:

```
${execi 300 curl -s http://192.168.1.124:8765/summary | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('ai_summary') or 'inference offline')
"}
```

numbers same pattern, one curl, parse the field you want
