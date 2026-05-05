from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn, os, uuid, re, httpx, asyncio
from datetime import datetime

app = FastAPI(title="Enriquece")

sessions = {}

def clean_cnpj(raw):
    return re.sub(r"\D", "", str(raw).strip())

def validate_cnpj(cnpj):
    c = clean_cnpj(cnpj)
    if len(c) != 14 or len(set(c)) == 1:
        return False
    def calc(c, n):
        weights = list(range(n, 1, -1)) + list(range(9, 1, -1))
        s = sum(int(c[i]) * weights[i] for i in range(n - 1))
        r = 11 - (s % 11)
        return 0 if r >= 10 else r
    return calc(c, 13) == int(c[12]) and calc(c, 14) == int(c[13])

def format_cnpj(c):
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}" if len(c) == 14 else c

@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "<h1>Enriquece online!</h1>"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/cnpj/upload")
async def upload(file: UploadFile = File(...)):
    import io
    content = await file.read()
    try:
        if file.filename.endswith(".csv"):
            import csv
            decoded = content.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(decoded))
            rows = list(reader)
        else:
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(content))
                ws = wb.active
                headers = [str(c.value) for c in ws[1]]
                rows = []
                for row in ws.iter_rows(min_row=2, values_only=True):
                    rows.append(dict(zip(headers, row)))
            except:
                return {"error": "Instale openpyxl"}
    except Exception as e:
        raise HTTPException(400, str(e))

    col = None
    for h in headers if not file.filename.endswith(".csv") else (rows[0].keys() if rows else []):
        sample = [str(r.get(h, "")) for r in rows[:10]]
        if sum(1 for v in sample if len(clean_cnpj(v)) >= 11) >= 2:
            col = h
            break
    if not col:
        col = list(rows[0].keys())[0] if rows else "CNPJ"

    seen = set()
    valid, dups, invalid = [], [], []
    for i, row in enumerate(rows):
        raw = str(row.get(col, ""))
        c = clean_cnpj(raw)
        if not validate_cnpj(c):
            invalid.append({"raw": raw, "row": i+2, "reason": "Inválido"})
        elif c in seen:
            dups.append({"raw": raw, "row": i+2, "reason": "Duplicado"})
        else:
            seen.add(c)
            valid.append({"cnpj": format_cnpj(c), "clean": c})

    sid = str(uuid.uuid4())
    sessions[sid] = {
        "status": "validated", "filename": file.filename,
        "valid": valid, "invalid": invalid + dups,
        "results": [], "total": len(rows)
    }
    return {
        "session_id": sid, "filename": file.filename,
        "total": len(rows), "valid": len(valid),
        "duplicates": len(dups), "invalid": len(invalid),
        "lots": max(1, -(-len(valid)//500)),
        "preview": [{"cnpj": r["cnpj"], "status": "válido"} for r in valid[:10]]
    }

@app.post("/api/enrich/start/{sid}")
def start(sid: str):
    if sid not in sessions:
        raise HTTPException(404)
    sessions[sid]["status"] = "processing"
    return {"ok": True}

@app.get("/api/enrich/stream/{sid}")
async def stream(sid: str):
    from fastapi.responses import StreamingResponse
    import json

    async def gen():
        if sid not in sessions:
            yield "data: " + json.dumps({"error": "Sessão não encontrada"}) + "\n\n"
            return
        s = sessions[sid]
        items = s["valid"]
        results = []
        hunter_key = os.getenv("HUNTER_API_KEY", "")
        apollo_key = os.getenv("APOLLO_API_KEY", "")

        async with httpx.AsyncClient(timeout=10) as client:
            for i, item in enumerate(items):
                r = {"cnpj": item["cnpj"], "clean": item["clean"],
                     "razao_social": "não encontrado", "nome_fantasia": "não encontrado",
                     "situacao": "não encontrado", "cnae_principal": "não encontrado",
                     "porte": "não encontrado", "cidade": "não encontrado",
                     "estado": "não encontrado", "cep": "não encontrado",
                     "endereco": "não encontrado", "telefone_cad": "não encontrado",
                     "email_cad": "não encontrado", "site": "não encontrado",
                     "linkedin_empresa": "não encontrado", "score_geral": "baixa",
                     "status": "sem dados", "decisores": [], "fontes": []}

                try:
                    resp = await client.get(f"https://brasilapi.com.br/api/cnpj/v1/{item['clean']}")
                    if resp.status_code == 200:
                        d = resp.json()
                        r["razao_social"] = d.get("razao_social", "não encontrado")
                        r["nome_fantasia"] = d.get("nome_fantasia", "") or r["razao_social"]
                        r["situacao"] = d.get("descricao_situacao_cadastral", "")
                        r["cnae_principal"] = d.get("cnae_fiscal_descricao", "")
                        r["porte"] = d.get("porte", "")
                        r["cidade"] = d.get("municipio", "")
                        r["estado"] = d.get("uf", "")
                        r["cep"] = d.get("cep", "")
                        r["telefone_cad"] = d.get("ddd_telefone_1", "")
                        r["email_cad"] = d.get("email", "")
                        addr = ", ".join(filter(None, [d.get("logradouro",""), d.get("numero",""), d.get("bairro","")]))
                        r["endereco"] = addr
                        r["fontes"].append({"dado": "Cadastral", "fonte": "BrasilAPI", "confianca": "alta"})
                        r["status"] = "parcial"
                except:
                    pass

                results.append(r)
                sessions[sid]["results"] = results
                pct = round((i+1)/len(items)*100)
                yield "data: " + json.dumps({
                    "status": "processing", "processed": i+1, "total": len(items),
                    "pct": pct, "current_cnpj": item["cnpj"],
                    "current_company": r["razao_social"],
                    "phones_found": sum(1 for x in results if x.get("telefone_cad","") not in ("","não encontrado")),
                    "emails_found": sum(1 for x in results if x.get("email_cad","") not in ("","não encontrado")),
                    "contacts_found": sum(1 for x in results if x.get("email_cad","") not in ("","não encontrado")),
                    "decisors_found": 0
                }) + "\n\n"
                await asyncio.sleep(0.1)

        sessions[sid]["status"] = "done"
        n = len(results)
        def pct(x): return round(x/n*100) if n else 0
        yield "data: " + json.dumps({
            "status": "done", "total": n,
            "pct_telefone": pct(sum(1 for x in results if x.get("telefone_cad","") not in ("","não encontrado"))),
            "pct_email": pct(sum(1 for x in results if x.get("email_cad","") not in ("","não encontrado"))),
            "pct_site": pct(sum(1 for x in results if x.get("site","") not in ("","não encontrado"))),
            "pct_decisor": 0, "pct_alta": pct(sum(1 for x in results if x.get("score_geral") == "alta")),
        }) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/enrich/results/{sid}")
def results(sid: str):
    if sid not in sessions:
        raise HTTPException(404)
    return {"results": sessions[sid].get("results", []), "status": sessions[sid].get("status")}

@app.post("/api/enrich/pause/{sid}")
def pause(sid: str):
    return {"ok": True}

@app.post("/api/enrich/resume/{sid}")
def resume(sid: str):
    return {"ok": True}

@app.get("/api/export/excel/{sid}")
def export(sid: str, filter: str = "all"):
    if sid not in sessions:
        raise HTTPException(404)
    results = sessions[sid].get("results", [])
    if filter == "alta":
        results = [r for r in results if r.get("score_geral") == "alta"]
    elif filter == "decisores":
        results = [r for r in results if r.get("decisores")]

    import tempfile
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Base Enriquecida"

    headers = ["CNPJ","Razão Social","Nome Fantasia","Situação","CNAE","Porte",
               "Cidade","Estado","CEP","Endereço","Telefone","E-mail","Site","Score"]
    fill = PatternFill("solid", fgColor="0D1B3E")
    font = Font(bold=True, color="FFFFFF", name="Arial")
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = fill; c.font = font
        c.alignment = Alignment(horizontal="center")

    for idx, r in enumerate(results, 2):
        vals = [r.get("cnpj",""), r.get("razao_social",""), r.get("nome_fantasia",""),
                r.get("situacao",""), r.get("cnae_principal",""), r.get("porte",""),
                r.get("cidade",""), r.get("estado",""), r.get("cep",""),
                r.get("endereco",""), r.get("telefone_cad",""), r.get("email_cad",""),
                r.get("site",""), r.get("score_geral","")]
        for j, v in enumerate(vals, 1):
            ws.cell(row=idx, column=j, value=v if v != "não encontrado" else "—")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:N{len(results)+1}"

    tmp = tempfile.mktemp(suffix=".xlsx")
    wb.save(tmp)
    return FileResponse(tmp,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"enriquece_{sid[:8]}.xlsx")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
