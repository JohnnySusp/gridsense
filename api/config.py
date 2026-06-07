from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    cassandra_host: str = "timeseries-db"
    cassandra_keyspace: str = "gridsense"

    neo4j_uri: str = "bolt://graph-db:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str

    postgres_dsn: str

    mongo_uri: str
    mongo_db: str = "gridsense"

    redis_url: str = "redis://cache:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()