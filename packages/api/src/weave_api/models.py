from pydantic import BaseModel


class Problems(BaseModel):
    problems: list[str]


class Detail(BaseModel):
    detail: str
