db = db.getSiblingDB("gridsense");

db.equipment.createIndex({ asset_id: 1 }, { unique: true });
db.equipment.createIndex({ equipment_type: 1 });
db.equipment.createIndex({ manufacturer: 1 });
