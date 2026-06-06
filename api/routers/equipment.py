from fastapi import APIRouter

router = APIRouter(prefix="/equipment", tags=["Equipment Catalog"])

@router.get("/ping")
async def equipment_ping():
    return {"router": "equipment", "status": "ok"}
