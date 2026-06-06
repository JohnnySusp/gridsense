from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from api.routers import alerts, billing, equipment, grid, sensors

app = FastAPI(title="GridSense API")

app.include_router(sensors.router)
app.include_router(grid.router)
app.include_router(equipment.router)
app.include_router(billing.router)
app.include_router(alerts.router)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "gridsense-api"}

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
