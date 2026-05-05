from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
import uvicorn, os, uuid, re, httpx, asyncio, io, json, tempfile
from datetime import datetime

app = FastAPI(title="Enriquece")
sessions = {}

def clean_cnpj(raw):
    c = re.sub(r"\D", "", str(raw).strip())
    if len(c) < 14:
        c = c.zfill(14)
    return c

def validate_cnpj(cnpj):
    c = clean_cnpj(cnpj)
    if len(c) != 14 or len(set(c)) == 1:
        return False
    w1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    s1 = sum(int(c[i]) * w1[i] for i in range(12))
    d1 = 0 if (11 - s1 % 11) >= 10 else (11 - s1 % 11)
    w2 = [6,5,4,3,2,9,8,7,6,5,4,3,2]
    s2 = sum(int(c[i]) * w2[i] for i in range(13))
    d2 = 0 if (11 - s2 % 11) >= 10 else (11 - s2 % 11)
    return int(c[12]) == d1 and int(c[13]) == d2

def format_cnpj(c):
    c = clean_cnpj(c)
    if len(c) == 14:
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    return c

@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "<h1>Enriquece online!</h1>"

@app.get("/health")
def health():
    return {"status": "ok", "produto": "Enriquece"}

@app.post("/api/cnpj/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    rows = []
    headers = []

    try:
        if file.filename.lower().endswith(".csv"):
            import csv
            decoded = content.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(decoded))
            rows = list(reader)
            headers = list(rows[0].keys()) if rows else []
        else:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
            ws = wb.active
            headers = [str(c.value) if c.value is not None else f"col{i}" for i, c in enumerate(ws[1])]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if any(v is not None for v in row):
                    rows.append(dict(zip(headers, row)))
    except Exception as e:
        raise HTTPException(400, f"Erro ao ler arquivo: {e}")

    # Detecta coluna CNPJ
    col = None
    for h in headers:
        sample = [str(r.get(h, "") or "") for r in rows[:15]]
        hits = sum(1 for v in sample if 11 <= len(clean_cnpj(v)) <= 14)
        if hits >= 1:
            col = h
            break
    if not col:
        col = headers[0] if headers else "CNPJ"

    seen = set()
    valid, dups, invalid = [], [], []
    for i, row in enumerate(rows):
        raw = str(row.get(col, "") or "")
        if not raw or raw == "None":
            continue
        c = clean_cnpj(raw)
        if not validate_cnpj(c):
            invalid.append({"raw": raw, "row": i + 2, "reason": "CNPJ inválido"})
        elif c in seen:
            dups.append({"raw": raw, "row": i + 2, "reason": "Duplicado"})
        else:
            seen.add(c)
            valid.append({"cnpj": format_cnpj(c), "clean": c})

    sid = str(uuid.uuid4())
    sessions[sid] = {
        "status": "validated", "filename": file.filename,
        "valid": valid, "invalid_rows": invalid + dups,
        "results": [], "total": len(rows)
    }

    return {
        "session_id": sid, "filename": file.filename,
        "total": len(rows), "valid": len(valid),
        "duplicates": len(dups), "invalid": len(invalid),
        "lots": max(1, -(-len(valid) // 500)),
        "preview": [{"cnpj": r["cnpj"], "status": "válido"} for r in valid[:10]]
    }

@app.post("/api/enrich/start/{sid}")
def start(sid: str, lot_index: int = 0):
    if sid not in sessions:
        raise HTTPException(404, "Sessão não encontrada")
    sessions[sid]["status"] = "processing"
    return {"ok": True}

@app.get("/api/enrich/stream/{sid}")
async def stream(sid: str, lot_index: int = 0):
    async def gen():
        if sid not in sessions:
            yield "data: " + json.dumps({"error": "Sessão não encontrada"}) + "\n\n"
            return

        s = sessions[sid]
        items = s["valid"]
        results = []

        async with httpx.AsyncClient(timeout=12) as client:
            for i, item in enumerate(items):
                r = {
                    "cnpj": item["cnpj"], "clean": item["clean"],
                    "razao_social": "não encontrado", "nome_fantasia": "não encontrado",
                    "situacao": "não encontrado", "cnae_principal": "não encontrado",
                    "natureza_jur": "não encontrado", "porte": "não encontrado",
                    "cidade": "não encontrado", "estado": "não encontrado",
                    "cep": "não encontrado", "endereco": "não encontrado",
                    "telefone_cad": "não encontrado", "email_cad": "não encontrado",
                    "site": "não encontrado", "linkedin_empresa": "não encontrado",
                    "score_geral": "baixa", "status": "sem dados",
                    "decisores": [], "fontes": []
                }

                # BrasilAPI
                try:
                    resp = await client.get(
                        f"https://brasilapi.com.br/api/cnpj/v1/{item['clean']}"
                    )
                    if resp.status_code == 200:
                        d = resp.json()
                        r["razao_social"]   = d.get("razao_social", "") or "não encontrado"
                        r["nome_fantasia"]  = d.get("nome_fantasia", "") or r["razao_social"]
                        r["situacao"]       = d.get("descricao_situacao_cadastral", "")
                        r["cnae_principal"] = d.get("cnae_fiscal_descricao", "")
                        r["natureza_jur"]   = d.get("descricao_natureza_juridica", "")
                        r["porte"]          = d.get("porte", "")
                        r["cidade"]         = d.get("municipio", "")
                        r["estado"]         = d.get("uf", "")
                        r["cep"]            = d.get("cep", "")
                        r["telefone_cad"]   = d.get("ddd_telefone_1", "") or "não encontrado"
                        r["email_cad"]      = d.get("email", "") or "não encontrado"
                        addr = ", ".join(filter(None, [
                            d.get("logradouro", ""), d.get("numero", ""), d.get("bairro", "")
                        ]))
                        r["endereco"] = addr or "não encontrado"
                        r["fontes"].append({
                            "dado": "Dados cadastrais", "fonte": "BrasilAPI",
                            "confianca": "alta", "data": datetime.now().strftime("%d/%m/%Y")
                        })
                        pontos = 0
                        if r["telefone_cad"] != "não encontrado": pontos += 2
                        if r["email_cad"] != "não encontrado": pontos += 2
                        if r["razao_social"] != "não encontrado": pontos += 2
                        r["score_geral"] = "alta" if pontos >= 5 else "media" if pontos >= 2 else "baixa"
                        r["status"] = "completo" if pontos >= 4 else "parcial"
                except Exception:
                    pass

                results.append(r)
                sessions[sid]["results"] = results

                pct = round((i + 1) / len(items) * 100)
                yield "data: " + json.dumps({
                    "status": "processing",
                    "processed": i + 1,
                    "total": len(items),
                    "pct": pct,
                    "current_cnpj": item["cnpj"],
                    "current_company": r["razao_social"],
                    "phones_found": sum(1 for x in results if x.get("telefone_cad", "") not in ("", "não encontrado")),
                    "emails_found": sum(1 for x in results if x.get("email_cad", "") not in ("", "não encontrado")),
                    "contacts_found": sum(1 for x in results if x.get("email_cad", "") not in ("", "não encontrado")),
                    "decisors_found": 0
                }) + "\n\n"
                await asyncio.sleep(0.1)

        sessions[sid]["status"] = "done"
        n = len(results)
        def p(x): return round(x / n * 100) if n else 0

        yield "data: " + json.dumps({
            "status": "done",
            "total": n,
            "pct_telefone": p(sum(1 for x in results if x.get("telefone_cad", "") not in ("", "não encontrado"))),
            "pct_email":    p(sum(1 for x in results if x.get("email_cad", "") not in ("", "não encontrado"))),
            "pct_site":     p(sum(1 for x in results if x.get("site", "") not in ("", "não encontrado"))),
            "pct_decisor":  0,
            "pct_alta":     p(sum(1 for x in results if x.get("score_geral") == "alta")),
        }) + "\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

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
    elif filter == "pendencias":
        results = [r for r in results if r.get("status") != "completo"]

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()

    # ABA 1 — Base Enriquecida
    ws1 = wb.active
    ws1.title = "Base Enriquecida"
    ws1.sheet_view.showGridLines = False

    headers = ["CNPJ", "Razão Social", "Nome Fantasia", "Situação", "CNAE Principal",
               "Natureza Jurídica", "Porte", "Cidade", "Estado", "CEP", "Endereço",
               "Telefone", "E-mail", "Site", "Score", "Status"]

    hfill = PatternFill("solid", fgColor="0D1B3E")
    hfont = Font(bold=True, color="FFFFFF", name="Arial", size=11)
    for i, h in enumerate(headers, 1):
        c = ws1.cell(row=1, column=i, value=h)
        c.fill = hfill
        c.font = hfont
        c.alignment = Alignment(horizontal="center", vertical="center")

    score_colors = {"alta": "E6F9F4", "media": "FFF8E6", "baixa": "FEF2F2"}
    for idx, r in enumerate(results, 2):
        bg = PatternFill("solid", fgColor="F4F6FB" if idx % 2 == 0 else "FFFFFF")
        vals = [
            r.get("cnpj", ""), r.get("razao_social", ""), r.get("nome_fantasia", ""),
            r.get("situacao", ""), r.get("cnae_principal", ""), r.get("natureza_jur", ""),
            r.get("porte", ""), r.get("cidade", ""), r.get("estado", ""),
            r.get("cep", ""), r.get("endereco", ""), r.get("telefone_cad", ""),
            r.get("email_cad", ""), r.get("site", ""),
            r.get("score_geral", "").upper(), r.get("status", "")
        ]
        for j, v in enumerate(vals, 1):
            cell = ws1.cell(row=idx, column=j, value=v if v != "não encontrado" else "—")
            cell.fill = bg
            cell.font = Font(name="Arial", size=10)
            if j == 15:  # Score
                sc = r.get("score_geral", "baixa")
                cell.fill = PatternFill("solid", fgColor=score_colors.get(sc, "F4F6FB"))
                cell.font = Font(bold=True, name="Arial", size=10)

    ws1.freeze_panes = "A2"
    ws1.auto_filter.ref = f"A1:P{len(results) + 1}"
    col_widths = [20, 35, 25, 16, 40, 30, 14, 18, 8, 14, 40, 18, 30, 30, 10, 12]
    for i, w in enumerate(col_widths, 1):
        from openpyxl.utils import get_column_letter
        ws1.column_dimensions[get_column_letter(i)].width = w

    # ABA 2 — Resumo
    ws2 = wb.create_sheet("Resumo")
    ws2.sheet_view.showGridLines = False
    ws2["A1"] = "ENRIQUECE — Resumo do Processamento"
    ws2["A1"].font = Font(bold=True, color="FFFFFF", size=14, name="Arial")
    ws2["A1"].fill = PatternFill("solid", fgColor="0D1B3E")
    ws2.merge_cells("A1:B1")
    ws2.row_dimensions[1].height = 35

    n = len(results)
    kv = [
        ("Total processado", n),
        ("Com telefone", sum(1 for r in results if r.get("telefone_cad", "") not in ("", "não encontrado"))),
        ("Com e-mail", sum(1 for r in results if r.get("email_cad", "") not in ("", "não encontrado"))),
        ("Com site", sum(1 for r in results if r.get("site", "") not in ("", "não encontrado"))),
        ("Alta confiança", sum(1 for r in results if r.get("score_geral") == "alta")),
        ("Data", datetime.now().strftime("%d/%m/%Y %H:%M")),
        ("Gerado por", "Enriquece · CredSeller"),
    ]
    for i, (k, v) in enumerate(kv, 3):
        ws2.cell(row=i, column=1, value=k).font = Font(bold=True, name="Arial", size=11)
        ws2.cell(row=i, column=2, value=v).font = Font(name="Arial", size=11)

    ws2.column_dimensions["A"].width = 25
    ws2.column_dimensions["B"].width = 30

    # ABA 3 — Pendências
    ws3 = wb.create_sheet("Pendências")
    ws3.sheet_view.showGridLines = False
    pend_headers = ["CNPJ", "Razão Social", "Campo Pendente", "Status"]
    for i, h in enumerate(pend_headers, 1):
        c = ws3.cell(row=1, column=i, value=h)
        c.fill = PatternFill("solid", fgColor="F59E0B")
        c.font = Font(bold=True, color="0D1B3E", name="Arial")
    row_p = 2
    for r in results:
        for campo, label in [("telefone_cad", "Telefone"), ("email_cad", "E-mail"), ("site", "Site")]:
            if r.get(campo, "") in ("", "não encontrado"):
                ws3.cell(row=row_p, column=1, value=r.get("cnpj", ""))
                ws3.cell(row=row_p, column=2, value=r.get("razao_social", ""))
                ws3.cell(row=row_p, column=3, value=label)
                ws3.cell(row=row_p, column=4, value="Não encontrado")
                row_p += 1

    tmp = tempfile.mktemp(suffix=".xlsx")
    wb.save(tmp)

    return FileResponse(
        tmp,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"enriquece_{sid[:8]}_{filter}.xlsx"
    )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
