from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn, os

app = FastAPI(title="Enriquece")

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

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
