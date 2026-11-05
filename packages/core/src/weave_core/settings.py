import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(os.environ.get("WEAVE_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/weave"))
