from fastapi import APIRouter

router = APIRouter(prefix="/alerts", tags=["Alerts"])

@router.get("/ping")
async def alerts_ping():
    return {"router": "alerts", "status": "ok"}
