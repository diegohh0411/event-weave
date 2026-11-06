from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """pgvector `vector` column with no fixed dimension; Python side is a list of floats."""
    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector"

    def bind_processor(self, dialect):
        def process(value):
            return None if value is None else "[" + ",".join(repr(float(x)) for x in value) + "]"
        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            return None if value is None else [float(x) for x in value.strip("[]").split(",") if x]
        return process
