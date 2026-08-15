from fastapi import FastAPI

app = FastAPI(
    title="StudioTech BI Report Engine",
    description="Deterministic analytics and interactive report generation",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)


@app.get("/")
def home():
    return {
        "service": "StudioTech BI Report Engine",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
