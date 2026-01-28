"""
Database type compatibility layer for multiple database backends.
Handles UUID, JSONB, ARRAY, and INET types across SQLite and PostgreSQL.
"""
from sqlalchemy import TypeDecorator, String, Text
import uuid
import json

# class UUIDEncoder(json.JSONEncoder):
#     """Custom JSON encoder that handles UUID objects"""
#     def default(self, obj):
#         if isinstance(obj, uuid.UUID):
#             return str(obj)
#         return super().default(obj)
    
class UUID(TypeDecorator):
    """Platform-independent UUID type.
    
    Uses BINARY(16) for SQLite and UUID for PostgreSQL.
    """
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == 'postgresql':
            return str(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


class JSONB(TypeDecorator):
    """Platform-independent JSONB type.
    
    Uses JSON for SQLite and JSONB for PostgreSQL.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value


class ARRAY(TypeDecorator):
    """Platform-independent ARRAY type.
    
    Uses JSON string representation for SQLite and ARRAY for PostgreSQL.
    """
    impl = Text
    cache_ok = True

    def __init__(self, item_type=None, **kwargs):
        super().__init__(**kwargs)
        self.item_type = item_type

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value


class INET(TypeDecorator):
    """Platform-independent INET type for IP addresses.
    
    Uses VARCHAR for both SQLite and PostgreSQL.
    """
    impl = String(45)
    cache_ok = True
