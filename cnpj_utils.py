from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn, os, uuid
from pathlib import Path

from routers import cnpj, enrich, export
from services.session import sessions

BASE = Path(__file__).parent.parent

app = FastAPI(title="Enriquece API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cnpj.router,    prefix="/api/cnpj",   tags=["cnpj"])
app.include_router(enrich.router,  prefix="/api/enrich", tags=["enrich"])
app.include_router(export.router,  prefix="/api/export", tags=["export"])

app.mount("/static", StaticFiles(directory=str(BASE / "frontend" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "frontend" / "templates"))

@app.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
def health():
    return {"status": "ok", "product": "Enriquece"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
