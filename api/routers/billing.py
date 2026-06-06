from fastapi import APIRouter

router = APIRouter(prefix="/billing", tags=["Billing"])

@router.get("/ping")
async def billing_ping():
    return {"router": "billing", "status": "ok"}
