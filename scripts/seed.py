import asyncio
import os
from pathlib import Path

from neo4j import GraphDatabase

from seed_postgres import seed_postgres
import seed_mongo
import seed_cassandra


def split_cypher_statements(text: str):
    for statement in text.split(";"):
        statement = statement.strip()
        if statement:
            yield statement


def seed_neo4j() -> None:
    uri = os.getenv("NEO4J_URI", "bolt://graph-db:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        raise RuntimeError("NEO4J_PASSWORD is not set.")

    candidates = [
        Path("/app/neo4j/import/seed.cypher"),
        Path(__file__).resolve().parents[1] / "neo4j" / "import" / "seed.cypher",
    ]

    cypher_path = next((p for p in candidates if p.exists()), None)
    if cypher_path is None:
        raise RuntimeError("Could not find neo4j/import/seed.cypher.")

    text = cypher_path.read_text(encoding="utf-8")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            for statement in split_cypher_statements(text):
                session.run(statement).consume()

            counts = session.run(
                """
                MATCH (s:Substation) WITH count(s) AS substations
                MATCH (t:Transformer) WITH substations, count(t) AS transformers
                MATCH (m:SmartMeter) WITH substations, transformers, count(m) AS smart_meters
                MATCH ()-[r]->()
                RETURN substations, transformers, smart_meters, count(r) AS relationships
                """
            ).single()

            print("Neo4j seed complete.")
            print(dict(counts))
    finally:
        driver.close()


async def main() -> None:
    print("Seeding Neo4j...")
    seed_neo4j()

    print("Seeding MongoDB...")
    seed_mongo.main()

    print("Seeding PostgreSQL...")
    await seed_postgres()

    print("Seeding Cassandra...")
    seed_cassandra.main()

    print("All seed steps completed.")


if __name__ == "__main__":
    asyncio.run(main())
