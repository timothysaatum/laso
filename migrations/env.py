import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import settings FIRST
from app.core.config import get_settings
settings = get_settings()

# Import Base BEFORE importing models
from app.db.base import Base

# NOW import all models - this ensures they're registered with Base
from app.models import (
    Organization, Branch, User, UserSession,
    Drug, DrugCategory, BranchInventory, DrugBatch, StockAdjustment,
    Customer, Prescription, Sale, SaleItem,
    Supplier, PurchaseOrder, PurchaseOrderItem,
    AuditLog, SystemAlert, SyncQueue
)

# Alembic Config
config = context.config
target_metadata = Base.metadata

# Logging setup
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    '''Run migrations in 'offline' mode'''
    url = settings.ALEMBIC_DB_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    '''Run migrations in 'online' mode'''
    url = settings.ALEMBIC_DB_URL
    
    configuration = {
        "sqlalchemy.url": url,
        "sqlalchemy.echo": False,
    }
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()