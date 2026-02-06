"""Snowflake connection management and query execution.

Provides an async-compatible client for executing queries against Snowflake
via the snowflake-connector-python driver. Uses a global singleton connection
with lazy initialization, and wraps synchronous connector calls in
``run_in_executor`` so they can be awaited from async FastAPI endpoints.

The connection is configured via ``config.get_settings()`` and uses the
SNOWFLAKE authenticator with OCSP fail-open mode for resilient certificate
validation.

Result rows are wrapped in ``CaseInsensitiveDict`` so that key lookups work
regardless of casing (e.g. ``row["ROW_COUNT"]`` and ``row["row_count"]`` both
work).  Original casing is preserved when iterating keys/items.
"""

import asyncio
import logging
import time
from typing import Any

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import DatabaseError, Error as SnowflakeError

from config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CaseInsensitiveDict — like requests.structures.CaseInsensitiveDict
# ---------------------------------------------------------------------------

class CaseInsensitiveDict(dict):
    """A dict subclass whose key lookups are case-insensitive.

    Stores items in the parent ``dict`` with their original casing so that
    ``json.dumps(row)`` preserves Snowflake's native column names.  A
    secondary ``_key_map`` (lowered → original) enables case-insensitive
    lookups: ``row["ROW_COUNT"]`` and ``row["row_count"]`` both work.

    Compatible with ``json.dumps`` (C encoder accesses parent dict directly).

    Example::

        >>> d = CaseInsensitiveDict(ROW_COUNT=42, table_type="VIEW")
        >>> d["row_count"]
        42
        >>> d["TABLE_TYPE"]
        'VIEW'
        >>> list(d.keys())
        ['ROW_COUNT', 'table_type']
        >>> json.dumps(d)  # works — uses parent dict
        '{"ROW_COUNT": 42, "table_type": "VIEW"}'
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # _key_map: lowered_key -> original_key (for case-insensitive lookup)
        self._key_map: dict[str, str] = {}
        # Populate via our __setitem__ to keep _key_map in sync
        source = dict(*args, **kwargs)
        for k, v in source.items():
            self[k] = v

    def __setitem__(self, key: str, value: Any) -> None:
        lowered = key.lower()
        # If this lowered key already exists with a different original key, remove old
        old_key = self._key_map.get(lowered)
        if old_key is not None and old_key != key:
            super().__delitem__(old_key)
        self._key_map[lowered] = key
        super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        original = self._key_map.get(key.lower())
        if original is None:
            raise KeyError(key)
        return super().__getitem__(original)

    def __delitem__(self, key: str) -> None:
        lowered = key.lower()
        original = self._key_map.pop(lowered, None)
        if original is None:
            raise KeyError(key)
        super().__delitem__(original)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key.lower() in self._key_map

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def copy(self) -> "CaseInsensitiveDict":
        return CaseInsensitiveDict(super().copy())

_connection: SnowflakeConnection | None = None


def _create_connection() -> SnowflakeConnection:
    """Create a new Snowflake connection from application settings.

    Configures OCSP fail-open so that transient certificate-validation
    issues do not block queries.

    Returns:
        A live ``SnowflakeConnection`` instance.

    Raises:
        SnowflakeError: If the connection cannot be established.
    """
    settings = get_settings()

    # Enable OCSP fail-open at the module level before connecting
    snowflake.connector.paramstyle = "qmark"

    conn = snowflake.connector.connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        password=settings.snowflake_password.get_secret_value(),
        warehouse=settings.snowflake_warehouse,
        database=settings.snowflake_database,
        role=settings.snowflake_role,
        authenticator="SNOWFLAKE",
        ocsp_fail_open=True,
        # Network timeouts (seconds) - configurable via env vars
        login_timeout=settings.snowflake_login_timeout,
        network_timeout=settings.snowflake_network_timeout,
        client_session_keep_alive=True,
    )
    logger.info(
        "Snowflake connection established — account=%s, user=%s, warehouse=%s, database=%s, role=%s",
        settings.snowflake_account,
        settings.snowflake_user,
        settings.snowflake_warehouse,
        settings.snowflake_database,
        settings.snowflake_role,
    )
    return conn


def get_connection() -> SnowflakeConnection:
    """Return the global singleton Snowflake connection, creating it lazily.

    If the Snowflake account is not configured (empty string), a
    ``RuntimeError`` is raised with a descriptive message.

    Returns:
        The shared ``SnowflakeConnection`` instance.

    Raises:
        RuntimeError: If Snowflake credentials are not configured.
        SnowflakeError: If the connection cannot be established.
    """
    global _connection

    settings = get_settings()
    if not settings.snowflake_account:
        raise RuntimeError(
            "Snowflake connection not configured. "
            "Set SNOWFLAKE_ACCOUNT and other SNOWFLAKE_* environment variables."
        )

    if _connection is None or _connection.is_closed():
        _connection = _create_connection()

    return _connection


_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds

# Transient error codes worth retrying
_TRANSIENT_ERROR_CODES = {
    250001,  # Connection reset
    250002,  # Network error
    250003,  # Timeout
    250005,  # Session expired
    390100,  # Request timeout
    390114,  # Service unavailable
}


def _is_transient(exc: Exception) -> bool:
    """Return True if the exception is a transient Snowflake error worth retrying."""
    if isinstance(exc, DatabaseError):
        errno = getattr(exc, "errno", None) or getattr(exc, "sfqid", None)
        if errno and isinstance(errno, int) and errno in _TRANSIENT_ERROR_CODES:
            return True
        msg = str(exc).lower()
        return any(kw in msg for kw in ("timeout", "connection reset", "connection aborted", "broken pipe"))
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    return False


def _execute_sync(
    sql: str,
    params: list[Any] | None = None,
) -> tuple[list[CaseInsensitiveDict], list[tuple[Any, ...]]]:
    """Execute a query synchronously and return (rows, description).

    This is the internal workhorse called from within ``run_in_executor``.
    Each row is returned as a ``CaseInsensitiveDict`` — keys preserve original
    casing from Snowflake but lookups are case-insensitive.

    Transient failures (network reset, timeout) are retried with exponential
    backoff up to ``_MAX_RETRIES`` times.

    Args:
        sql: The SQL statement to execute.
        params: Optional list of bind parameters.

    Returns:
        A tuple of (rows as list of CaseInsensitiveDict, cursor.description).
    """
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                description: list[tuple[Any, ...]] = list(cursor.description or [])
                columns = [col[0] for col in description]
                rows: list[CaseInsensitiveDict] = [
                    CaseInsensitiveDict(zip(columns, row))
                    for row in cursor.fetchall()
                ]
                return rows, description
            finally:
                cursor.close()

        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES and _is_transient(exc):
                wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Transient Snowflake error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)
                # Force reconnect on next attempt
                global _connection
                try:
                    if _connection and not _connection.is_closed():
                        _connection.close()
                except Exception:
                    pass
                _connection = None
            else:
                raise

    # Should not reach here, but satisfy type checker
    raise last_exc  # type: ignore[misc]


def execute_query_sync(
    sql: str,
    params: list[Any] | None = None,
) -> list[CaseInsensitiveDict]:
    """Execute a SQL query synchronously. Used by LangChain @tool functions.

    Returns rows as ``CaseInsensitiveDict`` — lookups are case-insensitive,
    original key casing preserved.
    """
    settings = get_settings()
    if not settings.snowflake_account:
        return []
    rows, _ = _execute_sync(sql, params)
    return rows


async def execute_query(
    sql: str,
    params: list[Any] | None = None,
) -> list[CaseInsensitiveDict]:
    """Execute a SQL query and return rows as case-insensitive dicts.

    Wraps the synchronous Snowflake connector in ``run_in_executor`` so it
    can be awaited from async endpoints without blocking the event loop.

    If Snowflake is not configured (empty account), returns an empty list
    rather than raising.

    Args:
        sql: The SQL statement to execute.
        params: Optional list of bind parameters.

    Returns:
        A list of ``CaseInsensitiveDict``, one per row.  Key lookups are
        case-insensitive; original casing is preserved on iteration.
    """
    settings = get_settings()
    if not settings.snowflake_account:
        logger.warning("Snowflake not configured — returning empty result for query")
        return []

    loop = asyncio.get_event_loop()
    try:
        rows, _ = await loop.run_in_executor(None, _execute_sync, sql, params)
        return rows
    except (DatabaseError, SnowflakeError) as exc:
        logger.error("Snowflake query failed: %s — SQL: %s", exc, sql[:200])
        raise
    except RuntimeError as exc:
        logger.error("Snowflake connection error: %s", exc)
        raise


async def execute_query_with_description(
    sql: str,
    params: list[Any] | None = None,
) -> tuple[list[CaseInsensitiveDict], list[tuple[Any, ...]]]:
    """Execute a SQL query and return both rows and cursor description.

    The description is a list of tuples matching the DB-API 2.0 spec:
    ``(name, type_code, display_size, internal_size, precision, scale, null_ok)``.

    If Snowflake is not configured (empty account), returns ``([], [])``.

    Args:
        sql: The SQL statement to execute.
        params: Optional list of bind parameters.

    Returns:
        A tuple of (rows as ``CaseInsensitiveDict``, cursor.description tuples).
    """
    settings = get_settings()
    if not settings.snowflake_account:
        logger.warning("Snowflake not configured — returning empty result for query")
        return [], []

    loop = asyncio.get_event_loop()
    try:
        rows, description = await loop.run_in_executor(None, _execute_sync, sql, params)
        return rows, description
    except (DatabaseError, SnowflakeError) as exc:
        logger.error("Snowflake query failed: %s — SQL: %s", exc, sql[:200])
        raise
    except RuntimeError as exc:
        logger.error("Snowflake connection error: %s", exc)
        raise


async def health_check() -> bool:
    """Check Snowflake connectivity by executing ``SELECT CURRENT_USER()``.

    Returns ``False`` gracefully if Snowflake is not configured or the
    connection fails.

    Returns:
        True if the query succeeds, False otherwise.
    """
    settings = get_settings()
    if not settings.snowflake_account:
        logger.debug("Snowflake health check skipped — not configured")
        return False

    try:
        rows = await execute_query("SELECT CURRENT_USER() AS current_user")
        if rows:
            logger.debug("Snowflake health check passed — user=%s", rows[0].get("current_user"))
            return True
        return False
    except Exception as exc:
        logger.warning("Snowflake health check failed: %s", exc)
        return False


async def close() -> None:
    """Close the global Snowflake connection if it exists.

    Safe to call multiple times; will silently no-op if already closed.
    """
    global _connection

    if _connection is not None and not _connection.is_closed():
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _connection.close)
            logger.info("Snowflake connection closed")
        except Exception as exc:
            logger.warning("Error closing Snowflake connection: %s", exc)
        finally:
            _connection = None
    else:
        _connection = None
