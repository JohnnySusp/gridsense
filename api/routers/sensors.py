from fastapi import APIRouter

router = APIRouter(prefix="/sensors", tags=["Sensors"])

@router.get("/ping")
async def sensors_ping():
    return {"router": "sensors", "status": "ok"}
