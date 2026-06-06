from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    cassandra_host: str = "timeseries-db"
    cassandra_keyspace: str = "gridsense"

    neo4j_uri: str = "bolt://graph-db:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    mongo_uri: str = "mongodb://catalog-db:27017/gridsense"
    mongo_db: str = "gridsense"

    postgres_dsn: str = "postgresql://gridsense:password@billing-db:5432/gridsense"

    redis_url: str = "redis://cache:6379/0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
