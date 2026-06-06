from fastapi import APIRouter

router = APIRouter(prefix="/grid", tags=["Grid Topology"])

@router.get("/ping")
async def grid_ping():
    return {"router": "grid", "status": "ok"}
