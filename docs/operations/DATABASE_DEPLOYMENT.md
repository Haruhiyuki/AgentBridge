# Database Deployment

AgentBridge defaults to in-memory state for local contract tests and demos. Durable
deployments should use `AGENTBRIDGE_DATABASE_URL`, run Alembic migrations explicitly, and
configure the SQLAlchemy connection pool for the database and process model.

## Recommended Startup Flow

1. Set `AGENTBRIDGE_DATABASE_URL` for the target database.
2. Run `uv run alembic upgrade head` before starting API or daemon processes.
3. Start AgentBridge processes with the same `AGENTBRIDGE_DATABASE_URL`.
4. Keep `AGENTBRIDGE_AUTO_CREATE_SCHEMA=false` outside local throwaway development.

For SQLite:

```bash
export AGENTBRIDGE_DATABASE_URL=sqlite:///./agentbridge.db
export AGENTBRIDGE_DATABASE_WRITE_LOCK_PATH=./agentbridge.db.write.lock
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory
```

For PostgreSQL:

```bash
export AGENTBRIDGE_DATABASE_URL=postgresql+psycopg://agentbridge:secret@127.0.0.1:5432/agentbridge
export AGENTBRIDGE_DATABASE_POOL_SIZE=5
export AGENTBRIDGE_DATABASE_MAX_OVERFLOW=10
export AGENTBRIDGE_DATABASE_POOL_TIMEOUT_SECONDS=30
export AGENTBRIDGE_DATABASE_POOL_RECYCLE_SECONDS=1800
export AGENTBRIDGE_DATABASE_POOL_PRE_PING=true
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory
```

Install the matching SQLAlchemy DBAPI driver for the selected URL scheme. For PostgreSQL,
`postgresql+psycopg://...` requires `psycopg`; `postgresql+psycopg2://...` requires
`psycopg2`.

## Runtime Environment Variables

- `AGENTBRIDGE_DATABASE_URL`: SQLAlchemy database URL. When unset, AgentBridge uses the
  in-memory repository.
- `AGENTBRIDGE_AUTO_CREATE_SCHEMA`: local-only convenience flag that calls
  `metadata.create_all()` at process startup. Prefer Alembic migrations for durable
  deployments.
- `AGENTBRIDGE_DATABASE_POOL_SIZE`: base SQLAlchemy pool size.
- `AGENTBRIDGE_DATABASE_MAX_OVERFLOW`: additional transient connections above pool size.
- `AGENTBRIDGE_DATABASE_POOL_TIMEOUT_SECONDS`: seconds to wait for a pooled connection.
- `AGENTBRIDGE_DATABASE_POOL_RECYCLE_SECONDS`: max connection age before recycling.
- `AGENTBRIDGE_DATABASE_POOL_PRE_PING`: enables SQLAlchemy stale-connection checks before
  checkout.
- `AGENTBRIDGE_DATABASE_ECHO`: enables SQL logging for local diagnostics. Keep it disabled
  in normal production logs because payloads may include operational metadata.
- `AGENTBRIDGE_DATABASE_WRITE_LOCK_PATH`: optional POSIX file lock path for the current
  snapshot repository. When set on every same-host API/daemon writer, each mutating
  operation takes the lock, reloads the latest database state, applies the mutation, and
  writes the updated snapshot back while holding the lock.

Unset pool variables use SQLAlchemy defaults for the selected dialect and pool class.

## Alembic Notes

`alembic/env.py` reads `AGENTBRIDGE_DATABASE_URL` before falling back to `alembic.ini`.
Migration commands use `NullPool`, so migration jobs do not compete with runtime pool
settings.

Recommended deployment shape:

- Run migrations once per release before rolling API/daemon processes.
- Keep migration logs with release artifacts.
- Avoid `AGENTBRIDGE_AUTO_CREATE_SCHEMA=true` for persistent environments.
- Back up the database before applying migrations that change storage semantics.

## Current Persistence Boundary

The current SQLAlchemy repository is still a write-through snapshot repository. It is
suitable for restart recovery and product-contract testing, but it rewrites broad state
snapshots after mutating operations.

For same-host deployments with more than one API/daemon writer, set the same
`AGENTBRIDGE_DATABASE_WRITE_LOCK_PATH` in every process before sharing one database. This
enforces a conservative single-writer policy around snapshot writes and prevents a stale
repository instance from overwriting state committed by another locked writer.

This lock is a bridge for the current snapshot model, not the final persistence
architecture. Product-grade multi-process deployments that need high write throughput or
cross-host writers still need row-level writes and stronger database transaction
boundaries.
