from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATA_DIR, DATABASE_URL

DATA_DIR.mkdir(parents=True, exist_ok=True)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def ensure_schema_compatibility(database_engine=engine) -> None:
    columns_to_add = {
        "products": {"warranty_expiry_date": "DATE"},
        "documents": {"suggested_warranty_expiry": "DATE"},
    }
    inspector = inspect(database_engine)
    with database_engine.begin() as connection:
        for table_name, columns in columns_to_add.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
