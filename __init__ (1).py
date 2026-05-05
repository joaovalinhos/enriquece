import os, re, asyncio, httpx
from datetime import datetime
from typing import Dict, Any, Optional

BRASIL_API   = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
RECEITAWS    = "https://www.receitaws.com.br/v1/cnpj/{cnpj}"
HUNTER_API   = "https://api.hunter.io/v2/domain-search"
APOLLO_API   = "https://api.apollo.io/v1/mixed_people/search"
GOOGLE_SEARCH = "https://www.googleapis.com/customsearch/v1"

HUNTER_KEY  = os.getenv("HUNTER_API_KEY", "")
APOLLO_KEY  = os.getenv("APOLLO_API_KEY", "")
GOOGLE_KEY  = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CX   = os.getenv("GOOGLE_CX", "")

DECISION_MAKER_TITLES = [
    "Diretor de Parcerias","Head de Parcerias","Diretor Comercial","Head Comercial",
    "CFO","Diretor Financeiro","Head Financeiro","Diretor de Marketplace",
    "Head de Marketplace","Diretor de Sellers","Head de Sellers",
    "Diretor de Payments","Head de Pagamentos","Diretor de Produto",
    "Head de Produto","Diretor de Revenue","Head de Growth",
    "Gerente de Parcerias","Gerente Comercial","Gerente Financeiro",
]

async def fetch_brasilapi(cnpj_clean: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        r = await client.get(BRASIL_API.format(cnpj=cnpj_clean), timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

async def fetch_receitaws(cnpj_clean: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        r = await client.get(RECEITAWS.format(cnpj=cnpj_clean), timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def parse_cadastral(data: Dict, source: str) -> Dict:
    if not data:
        return {}
    # BrasilAPI shape
    if "razao_social" in data:
        addr_parts = [
            data.get("logradouro",""), data.get("numero",""),
            data.get("complemento",""), data.get("bairro",""),
        ]
        endereco = ", ".join(p for p in addr_parts if p)
        return {
            "razao_social":   data.get("razao_social",""),
            "nome_fantasia":  data.get("nome_fantasia",""),
            "situacao":       data.get("descricao_situacao_cadastral",""),
            "data_abertura":  data.get("data_inicio_atividade",""),
            "cnae_principal": (data.get("cnae_fiscal_descricao","") or
                               str(data.get("cnae_fiscal",""))),
            "natureza_jur":   data.get("descricao_natureza_juridica",""),
            "porte":          data.get("porte",""),
            "cidade":         data.get("municipio",""),
            "estado":         data.get("uf",""),
            "cep":            data.get("cep",""),
            "endereco":       endereco,
            "telefone_cad":   data.get("ddd_telefone_1",""),
            "email_cad":      data.get("email",""),
            "_source":        source,
            "_confidence":    "alta",
        }
    # ReceitaWS shape
    if "nome" in data:
        return {
            "razao_social":   data.get("nome",""),
            "nome_fantasia":  data.get("fantasia",""),
            "situacao":       data.get("situacao",""),
            "data_abertura":  data.get("abertura",""),
            "cnae_principal": data.get("atividade_principal",[{}])[0].get("text",""),
            "natureza_jur":   data.get("natureza_juridica",""),
            "porte":          data.get("porte",""),
            "cidade":         data.get("municipio",""),
            "estado":         data.get("uf",""),
            "cep":            data.get("cep",""),
            "endereco":       data.get("logradouro",""),
            "telefone_cad":   data.get("telefone",""),
            "email_cad":      data.get("email",""),
            "_source":        source,
            "_confidence":    "alta",
        }
    return {}

async def search_google(query: str, client: httpx.AsyncClient) -> list:
    if not GOOGLE_KEY or not GOOGLE_CX:
        return []
    try:
        r = await client.get(GOOGLE_SEARCH, params={
            "key": GOOGLE_KEY, "cx": GOOGLE_CX, "q": query, "num": 5
        }, timeout=8)
        if r.status_code == 200:
            return r.json().get("items", [])
    except Exception:
        pass
    return []

def extract_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else ""

async def fetch_hunter(domain: str, company: str, client: httpx.AsyncClient) -> Dict:
    if not HUNTER_KEY or not domain:
        return {}
    try:
        r = await client.get(HUNTER_API, params={
            "domain": domain, "api_key": HUNTER_KEY, "limit": 5
        }, timeout=10)
        if r.status_code == 200:
            d = r.json().get("data", {})
            emails = [e for e in d.get("emails", []) if e.get("type") == "professional"]
            people = []
            for e in emails:
                fn = e.get("first_name","") + " " + e.get("last_name","")
                people.append({
                    "nome": fn.strip(),
                    "cargo": e.get("position",""),
                    "email": e.get("value",""),
                    "linkedin": e.get("linkedin",""),
                    "confidence": e.get("confidence", 0),
                    "_source": "Hunter.io",
                    "_confidence": "alta" if e.get("confidence",0) > 70 else "media",
                })
            return {"domain": domain, "people": people, "_source": "Hunter.io"}
    except Exception:
        pass
    return {}

async def fetch_apollo(company: str, domain: str, client: httpx.AsyncClient) -> Dict:
    if not APOLLO_KEY:
        return {}
    try:
        payload = {
            "api_key": APOLLO_KEY,
            "q_organization_name": company,
            "organization_domains": [domain] if domain else [],
            "person_titles": DECISION_MAKER_TITLES,
            "page": 1, "per_page": 5,
        }
        r = await client.post(APOLLO_API, json=payload, timeout=12)
        if r.status_code == 200:
            people = []
            for p in r.json().get("people", []):
                org = (p.get("organization") or {})
                people.append({
                    "nome": p.get("name",""),
                    "cargo": p.get("title",""),
                    "area": p.get("departments",[""])[0] if p.get("departments") else "",
                    "email": p.get("email",""),
                    "linkedin": p.get("linkedin_url",""),
                    "phone": p.get("phone_numbers",[{}])[0].get("raw_number","") if p.get("phone_numbers") else "",
                    "_source": "Apollo",
                    "_confidence": "alta" if p.get("email") else "media",
                })
            return {"people": people, "_source": "Apollo"}
    except Exception:
        pass
    return {}

def score_decisor(cargo: str) -> str:
    cargo_l = cargo.lower()
    high = ["parcerias","comercial","financeiro","cfo","marketplace","sellers","payments","pagamento","revenue","growth"]
    med  = ["produto","operações","operacoes"]
    for h in high:
        if h in cargo_l: return "Alta"
    for m in med:
        if m in cargo_l: return "Média"
    return "Baixa"

def motivo_abordagem(cargo: str) -> str:
    c = cargo.lower()
    if "parcerias" in c: return "Decisor direto de parcerias comerciais e integrações"
    if "comercial" in c: return "Responsável pela área de vendas e receita"
    if "financeiro" in c or "cfo" in c: return "Decisor sobre crédito e antecipação de recebíveis"
    if "marketplace" in c or "sellers" in c: return "Gestão de sellers e ecossistema — cliente potencial CredSeller"
    if "pagamento" in c or "payment" in c: return "Área de meios de pagamento e arranjos fechados"
    if "growth" in c or "revenue" in c: return "Responsável por crescimento e novas receitas"
    return "Contato relevante para abordagem comercial"

async def enrich_cnpj(cnpj_clean: str, cnpj_fmt: str) -> Dict[str, Any]:
    result = {
        "cnpj": cnpj_fmt,
        "clean": cnpj_clean,
        "razao_social": "não encontrado",
        "nome_fantasia": "não encontrado",
        "situacao": "não encontrado",
        "data_abertura": "não encontrado",
        "cnae_principal": "não encontrado",
        "natureza_jur": "não encontrado",
        "porte": "não encontrado",
        "cidade": "não encontrado",
        "estado": "não encontrado",
        "cep": "não encontrado",
        "endereco": "não encontrado",
        "telefone_cad": "não encontrado",
        "telefone_extra": "não encontrado",
        "whatsapp": "não encontrado",
        "email_cad": "não encontrado",
        "email_comercial": "não encontrado",
        "email_financeiro": "não encontrado",
        "email_parcerias": "não encontrado",
        "site": "não encontrado",
        "linkedin_empresa": "não encontrado",
        "instagram": "não encontrado",
        "score_geral": "baixa",
        "status": "parcial",
        "fontes": [],
        "decisores": [],
        "observacoes": "",
        "_ts": datetime.utcnow().isoformat(),
    }

    async with httpx.AsyncClient(headers={"User-Agent": "Enriquece/1.0"}) as client:
        # 1. Dados cadastrais
        cad = await fetch_brasilapi(cnpj_clean, client)
        src = "BrasilAPI"
        if not cad:
            cad = await fetch_receitaws(cnpj_clean, client)
            src = "ReceitaWS"

        if cad:
            parsed = parse_cadastral(cad, src)
            for k, v in parsed.items():
                if not k.startswith("_") and v:
                    result[k] = v
            result["fontes"].append({
                "dado": "Dados cadastrais", "fonte": src,
                "url": (BRASIL_API if src=="BrasilAPI" else RECEITAWS).format(cnpj=cnpj_clean),
                "tipo": "Base pública", "confianca": "alta",
                "data": datetime.utcnow().strftime("%d/%m/%Y"),
            })

        company = result["razao_social"] if result["razao_social"] != "não encontrado" else ""
        fantasia = result["nome_fantasia"] if result["nome_fantasia"] != "não encontrado" else company

        # 2. Google — site e LinkedIn
        if company:
            items = await search_google(f'"{fantasia or company}" site oficial', client)
            for item in items:
                link = item.get("link","")
                if link and "receitaws" not in link and "brasilapi" not in link:
                    if result["site"] == "não encontrado":
                        result["site"] = link
                        result["fontes"].append({
                            "dado": "Site oficial", "fonte": "Google Search",
                            "url": link, "tipo": "Busca web", "confianca": "media",
                            "data": datetime.utcnow().strftime("%d/%m/%Y"),
                        })
                    break

            li_items = await search_google(f'"{fantasia or company}" site:linkedin.com/company', client)
            for item in li_items:
                if "linkedin.com/company" in item.get("link",""):
                    result["linkedin_empresa"] = item["link"]
                    result["fontes"].append({
                        "dado": "LinkedIn empresa", "fonte": "Google Search",
                        "url": item["link"], "tipo": "Rede social", "confianca": "alta",
                        "data": datetime.utcnow().strftime("%d/%m/%Y"),
                    })
                    break

        # 3. Domain
        domain = ""
        if result["site"] != "não encontrado":
            domain = extract_domain(result["site"])

        # 4. Hunter.io — e-mails e contatos
        if domain or company:
            hunter = await fetch_hunter(domain, company, client)
            if hunter.get("people"):
                for p in hunter["people"][:3]:
                    cargo_l = (p.get("cargo","")).lower()
                    if "financeiro" in cargo_l or "finance" in cargo_l:
                        result["email_financeiro"] = p.get("email","")
                    elif "comercial" in cargo_l or "sales" in cargo_l or "venda" in cargo_l:
                        result["email_comercial"] = p.get("email","")
                    elif "parceria" in cargo_l or "partner" in cargo_l:
                        result["email_parcerias"] = p.get("email","")
                    elif result["email_comercial"] == "não encontrado":
                        result["email_comercial"] = p.get("email","")

                result["fontes"].append({
                    "dado": "E-mails profissionais", "fonte": "Hunter.io",
                    "url": f"https://hunter.io/domain/{domain}",
                    "tipo": "Enriquecimento B2B", "confianca": "alta",
                    "data": datetime.utcnow().strftime("%d/%m/%Y"),
                })
                for p in hunter["people"][:3]:
                    if p.get("nome") and p.get("cargo"):
                        result["decisores"].append({
                            "nome": p["nome"], "cargo": p["cargo"],
                            "area": p.get("area",""), "email": p.get("email",""),
                            "linkedin": p.get("linkedin",""), "phone": "",
                            "prioridade": score_decisor(p["cargo"]),
                            "motivo": motivo_abordagem(p["cargo"]),
                            "score": p.get("_confidence","media"),
                            "fonte": "Hunter.io",
                        })

        # 5. Apollo — decisores
        if company or domain:
            apollo = await fetch_apollo(company, domain, client)
            if apollo.get("people"):
                for p in apollo["people"][:5]:
                    existing = [d["nome"] for d in result["decisores"]]
                    if p.get("nome") and p["nome"] not in existing:
                        result["decisores"].append({
                            "nome": p["nome"], "cargo": p.get("cargo",""),
                            "area": p.get("area",""), "email": p.get("email",""),
                            "linkedin": p.get("linkedin",""), "phone": p.get("phone",""),
                            "prioridade": score_decisor(p.get("cargo","")),
                            "motivo": motivo_abordagem(p.get("cargo","")),
                            "score": p.get("_confidence","media"),
                            "fonte": "Apollo",
                        })
                if apollo["people"]:
                    result["fontes"].append({
                        "dado": "Decisores", "fonte": "Apollo",
                        "url": "https://app.apollo.io", "tipo": "Enriquecimento B2B",
                        "confianca": "alta", "data": datetime.utcnow().strftime("%d/%m/%Y"),
                    })

        # 6. Score geral
        pontos = 0
        if result["telefone_cad"] != "não encontrado": pontos += 2
        if result["email_cad"] != "não encontrado" or result["email_comercial"] != "não encontrado": pontos += 2
        if result["site"] != "não encontrado": pontos += 2
        if result["linkedin_empresa"] != "não encontrado": pontos += 1
        if result["decisores"]: pontos += 3
        result["score_geral"] = "alta" if pontos >= 7 else "media" if pontos >= 4 else "baixa"
        result["status"] = "completo" if pontos >= 7 else "parcial" if pontos >= 2 else "sem dados"

    return result
