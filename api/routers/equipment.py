from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.mongo import MongoStore, get_mongo
from api.models.mongo import EquipmentCreate, EquipmentOut, EquipmentUpdate

router = APIRouter(prefix="/equipment", tags=["Equipment Catalog"])


def _serialize_equipment(document: dict[str, Any]) -> dict[str, Any]:
    data = dict(document)
    object_id = data.pop("_id", None)
    data["id"] = str(object_id) if object_id is not None else None
    return data


@router.get("/ping")
async def equipment_ping() -> dict[str, str]:
    return {"router": "equipment", "status": "ok"}


@router.post(
    "",
    response_model=EquipmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create equipment catalog record in MongoDB",
)
async def create_equipment(
    payload: EquipmentCreate,
    mongo: Annotated[MongoStore, Depends(get_mongo)],
) -> dict[str, Any]:
    document = payload.model_dump()
    try:
        result = await mongo.equipment.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="asset_id already exists") from exc

    saved = await mongo.equipment.find_one({"_id": result.inserted_id})
    if saved is None:
        raise HTTPException(status_code=500, detail="Equipment record could not be read after insert")
    return _serialize_equipment(saved)


@router.get(
    "",
    response_model=list[EquipmentOut],
    summary="List equipment catalog records",
)
async def list_equipment(
    mongo: Annotated[MongoStore, Depends(get_mongo)],
    equipment_type: str | None = None,
    manufacturer: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {}
    if equipment_type is not None:
        filters["equipment_type"] = equipment_type
    if manufacturer is not None:
        filters["manufacturer"] = manufacturer
    if status_filter is not None:
        filters["status"] = status_filter

    cursor = mongo.equipment.find(filters).sort("asset_id", 1).skip(offset).limit(limit)
    return [_serialize_equipment(document) async for document in cursor]


@router.get(
    "/{asset_id}",
    response_model=EquipmentOut,
    summary="Get one equipment catalog record by asset_id",
)
async def get_equipment(
    asset_id: str,
    mongo: Annotated[MongoStore, Depends(get_mongo)],
) -> dict[str, Any]:
    document = await mongo.equipment.find_one({"asset_id": asset_id})
    if document is None:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return _serialize_equipment(document)


@router.patch(
    "/{asset_id}",
    response_model=EquipmentOut,
    summary="Patch one equipment catalog record by asset_id",
)
async def update_equipment(
    asset_id: str,
    payload: EquipmentUpdate,
    mongo: Annotated[MongoStore, Depends(get_mongo)],
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        document = await mongo.equipment.find_one({"asset_id": asset_id})
    else:
        document = await mongo.equipment.find_one_and_update(
            {"asset_id": asset_id},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )

    if document is None:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return _serialize_equipment(document)


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one equipment catalog record by asset_id",
)
async def delete_equipment(
    asset_id: str,
    mongo: Annotated[MongoStore, Depends(get_mongo)],
) -> Response:
    result = await mongo.equipment.delete_one({"asset_id": asset_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
