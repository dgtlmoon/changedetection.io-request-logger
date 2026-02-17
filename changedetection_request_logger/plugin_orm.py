"""
Database request logging plugin for changedetection.io using SQLAlchemy ORM.

Works with MySQL, PostgreSQL, SQLite - database agnostic!
Schema managed by Alembic migrations.
"""
from changedetectionio.pluggy_interface import hookimpl
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool
import os
import time
import json
import base64
import brotli
from datetime import datetime, date

from .models import (
    Base, WatchRequest,
    get_or_create_hostname,
    get_or_create_proxy,
    get_or_create_browser_conn,
    get_or_create_watch,
    get_or_create_error_type
)


# Global session factory (initialized on first use)
_session_factory = None
_engine = None
_config_error_logged = False


def get_database_url():
    """Build database URL from LOGGER_* environment variables.

    Supports MySQL, PostgreSQL, SQLite.

    Returns:
        str: SQLAlchemy database URL or None if not configured
    """
    global _config_error_logged

    db_type = os.getenv('LOGGER_DB_TYPE', 'mysql').lower()
    password = os.getenv('LOGGER_MYSQL_PASSWORD') or os.getenv('LOGGER_POSTGRES_PASSWORD')

    if not password and db_type != 'sqlite':
        if not _config_error_logged:
            logger.error(f"Request logger plugin is not configured - missing password for LOGGER_DB_TYPE='{db_type}'. Set LOGGER_MYSQL_PASSWORD or LOGGER_POSTGRES_PASSWORD, or use LOGGER_DB_TYPE=sqlite")
            _config_error_logged = True
        return None

    if db_type == 'mysql':
        host = os.getenv('LOGGER_MYSQL_HOST', 'localhost')
        port = os.getenv('LOGGER_MYSQL_PORT', '3306')
        user = os.getenv('LOGGER_MYSQL_USER', 'changedetection')
        database = os.getenv('LOGGER_MYSQL_DATABASE', 'changedetection_logs')
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"

    elif db_type == 'postgresql':
        host = os.getenv('LOGGER_POSTGRES_HOST', 'localhost')
        port = os.getenv('LOGGER_POSTGRES_PORT', '5432')
        user = os.getenv('LOGGER_POSTGRES_USER', 'changedetection')
        database = os.getenv('LOGGER_POSTGRES_DB', 'changedetection_logs')
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"

    elif db_type == 'sqlite':
        db_path = os.getenv('LOGGER_SQLITE_PATH', '/tmp/changedetection_logs.db')
        return f"sqlite:///{db_path}"

    else:
        if not _config_error_logged:
            logger.critical(f"Unsupported LOGGER_DB_TYPE: {db_type}")
            _config_error_logged = True
        return None


def get_session_factory():
    """Get or create SQLAlchemy session factory with connection pooling.

    Returns:
        scoped_session or None
    """
    global _session_factory, _engine

    if _session_factory is None:
        try:
            db_url = get_database_url()
            if not db_url:
                return None

            # Create engine with connection pooling
            pool_size = int(os.getenv('LOGGER_DB_POOL_SIZE', 5))
            _engine = create_engine(
                db_url,
                poolclass=QueuePool,
                pool_size=pool_size,
                max_overflow=10,
                pool_pre_ping=True,  # Verify connections before using
                echo=False
            )

            # Create tables if they don't exist
            Base.metadata.create_all(_engine)

            # Create session factory
            session_factory = sessionmaker(bind=_engine)
            _session_factory = scoped_session(session_factory)

            logger.info(f"SQLAlchemy session factory initialized for {db_url.split('@')[0].split('://')[0]}")

        except Exception as e:
            logger.critical(f"Failed to initialize SQLAlchemy: {e}")
            return None

    return _session_factory


def compress_browser_steps(browser_steps):
    """Compress browser steps to brotli format.

    Args:
        browser_steps: List of browser step dicts

    Returns:
        bytes: Brotli-compressed JSON bytes, or None
    """
    if not browser_steps:
        return None

    try:
        json_data = json.dumps(browser_steps)
        compressed = brotli.compress(json_data.encode('utf-8'), quality=6)
        return compressed

    except Exception as e:
        logger.critical(f"Failed to compress browser_steps: {e}")
        return None


class MySQLLoggerWrapper:
    """Wrapper that logs all update_handler operations using SQLAlchemy ORM."""

    def __init__(self, wrapped_handler, watch, datastore):
        """
        Args:
            wrapped_handler: The original update_handler instance
            watch: The watch dict being processed
            datastore: Application datastore
        """
        self.wrapped_handler = wrapped_handler
        self.watch = watch
        self.datastore = datastore
        self.hostname = os.getenv('HOSTNAME') or 'unknown'
        self.app_guid = datastore.data['settings']['application'].get('shared_diff_access_password', 'default-guid')

        # Metrics to capture
        self.start_time = time.time()
        self.fetch_complete = False
        self.detection_complete = False
        self.content_length = None
        self.status_code = None
        self.error_type = None
        self.error_message = None
        self.changed = False
        self.browser_connection_url = None

        # Store the database insert ID for finalization hook
        self.last_logging_insert_id = None

    def __getattr__(self, name):
        """Proxy all attribute access to the wrapped handler."""
        return getattr(self.wrapped_handler, name)

    def _log_to_database(self):
        """Log the complete request using SQLAlchemy ORM.

        CRITICAL: Never raises exceptions - always catches and logs errors.
        """
        SessionFactory = get_session_factory()
        if not SessionFactory:
            return

        session = None

        try:
            session = SessionFactory()

            # Calculate metrics
            duration_ms = int((time.time() - self.start_time) * 1000)

            # Determine result status
            if self.error_type:
                result = 'failed'
            elif self.fetch_complete and self.detection_complete:
                result = 'success'
            elif self.fetch_complete:
                result = 'partial'
            else:
                result = 'incomplete'

            # Compress browser steps if present
            browser_steps_compressed = None
            browser_steps_count = 0
            browser_steps = self.watch.get('browser_steps')
            if browser_steps:
                browser_steps_compressed = compress_browser_steps(browser_steps)
                browser_steps_count = len(browser_steps)

            # Get proxy info
            proxy_key = self.watch.get('proxy')
            proxy_endpoint = None
            if hasattr(self.wrapped_handler, 'fetcher'):
                proxy_endpoint = getattr(self.wrapped_handler.fetcher, 'proxy', None)
            if proxy_key and (proxy_key.startswith('http://') or proxy_key.startswith('socks')):
                proxy_endpoint = proxy_key
                proxy_key = None

            # Get browser connection URL
            fetch_backend = self.watch.get('fetch_backend', 'system')
            if self.browser_connection_url:
                browser_conn_url = self.browser_connection_url
            else:
                browser_conn_url = None

            # Get or create lookup entries
            hostname_obj = get_or_create_hostname(session, self.hostname)
            # Use watch.link for canonical URL (handles redirects, etc)
            watch_url = getattr(self.watch, 'link', None) or self.watch.get('url')
            watch_obj = get_or_create_watch(
                session,
                self.watch.get('uuid'),
                watch_url,
                self.watch.get('processor', 'text_json_diff')
            )
            proxy_obj = get_or_create_proxy(session, proxy_key, proxy_endpoint) if proxy_endpoint else None
            browser_conn_obj = get_or_create_browser_conn(session, browser_conn_url, fetch_backend) if browser_conn_url else None
            error_type_obj = get_or_create_error_type(session, self.error_type) if self.error_type else None

            # Create main request record
            request = WatchRequest(
                app_guid=self.app_guid,
                hostname_id=hostname_obj.id,
                watch_id=watch_obj.id,
                request_date=date.today(),
                request_timestamp=datetime.now(),
                proxy_id=proxy_obj.id if proxy_obj else None,
                browser_conn_id=browser_conn_obj.id if browser_conn_obj else None,
                browser_steps=browser_steps_compressed,
                browser_steps_count=browser_steps_count,
                result=result,
                duration_ms=duration_ms,
                content_length=self.content_length,
                status_code=self.status_code,
                error_type_id=error_type_obj.id if error_type_obj else None,
                error_message=self.error_message
            )

            session.add(request)
            session.commit()

            # Store the insert ID for later use (e.g., updating status in finalize hook)
            self.last_logging_insert_id = request.id
            logger.debug(f"Logged watch {self.watch.get('uuid')} with request ID {request.id}")

        except Exception as e:
            logger.critical(f"SQLAlchemy logging failed for watch {self.watch.get('uuid')}: {e}")
            if session:
                session.rollback()

        finally:
            if session:
                try:
                    session.close()
                except Exception as e:
                    logger.critical(f"SQLAlchemy session cleanup failed: {e}")

    async def call_browser(self):
        """Wrapped call_browser with logging."""
        try:
            result = await self.wrapped_handler.call_browser()

            # Capture metrics
            self.fetch_complete = True
            if hasattr(self.wrapped_handler, 'fetcher'):
                fetcher = self.wrapped_handler.fetcher
                self.content_length = len(getattr(fetcher, 'content', b''))
                self.status_code = getattr(fetcher, 'status_code', None)

                # Try to capture browser connection URL
                if hasattr(fetcher, 'browser_connection_url'):
                    self.browser_connection_url = fetcher.browser_connection_url
                elif hasattr(fetcher, 'command_executor'):
                    # Selenium
                    self.browser_connection_url = str(fetcher.command_executor._url)
                elif hasattr(fetcher, 'browser') and hasattr(fetcher.browser, 'wsEndpoint'):
                    # Playwright/Puppeteer
                    self.browser_connection_url = fetcher.browser.wsEndpoint

            return result

        except Exception as e:
            # Capture error but re-raise it
            self.error_type = type(e).__name__
            self.error_message = str(e)[:1000]  # Truncate to 1000 chars
            raise

    def run_changedetection(self, watch=None, force_reprocess=False):
        """Wrapped run_changedetection with logging."""
        try:
            changed, update_obj, contents = self.wrapped_handler.run_changedetection(
                watch=watch or self.watch,
                force_reprocess=force_reprocess
            )

            # Capture metrics
            self.detection_complete = True
            self.changed = changed
            if not self.content_length and contents:
                self.content_length = len(contents)

            # Log to database after everything completes
            self._log_to_database()

            return changed, update_obj, contents

        except Exception as e:
            # Capture error and log before re-raising
            if not self.error_type:  # Only set if not already set by call_browser
                self.error_type = type(e).__name__
                self.error_message = str(e)[:1000]

            # Log even on failure
            self._log_to_database()

            raise


@hookimpl
def update_handler_alter(update_handler, watch, datastore):
    """Wrap the update_handler to add SQLAlchemy logging.

    This hook is called after the update_handler is created but before
    it processes the watch.

    Args:
        update_handler: The perform_site_check instance
        watch: The watch dict being processed
        datastore: The application datastore

    Returns:
        MySQLLoggerWrapper or None: Wrapped handler or None to keep original
    """
    try:
        SessionFactory = get_session_factory()
        if not SessionFactory:
            # Database not configured - skip logging
            return None

        logger.debug(f"Wrapping update_handler with SQLAlchemy logger for watch {watch.get('uuid')}")
        return MySQLLoggerWrapper(update_handler, watch, datastore)

    except Exception as e:
        logger.critical(f"Failed to wrap update_handler for SQLAlchemy logging: {e}")
        # Return None to use original handler - never block on plugin failure
        return None


@hookimpl
def update_finalize(update_handler, watch, datastore, processing_exception):
    """Update the final status of the request after processing completes.

    This hook is called in the finally block after all processing and cleanup.
    It updates the database record with the final result status based on whether
    the processing succeeded or failed.

    Args:
        update_handler: The perform_site_check instance (may be None or wrapper)
        watch: The watch dict that was processed (may be None)
        datastore: The application datastore
        processing_exception: The exception from processing, or None if successful

    Returns:
        None
    """
    try:
        SessionFactory = get_session_factory()
        if not SessionFactory:
            return

        # Check if we have a logging insert_id to update
        if not update_handler or not hasattr(update_handler, 'last_logging_insert_id'):
            return

        insert_id = update_handler.last_logging_insert_id
        if not insert_id:
            return

        # Determine final result status
        if processing_exception is None:
            final_result = 'success'
            logger.debug(f"Finalizing request {insert_id} as SUCCESS")
        else:
            final_result = 'failed'
            logger.debug(f"Finalizing request {insert_id} as FAILED: {str(processing_exception)[:100]}")

        # Update the database record
        session = None
        try:
            session = SessionFactory()

            # Update the result field
            request = session.query(WatchRequest).filter_by(id=insert_id).first()
            if request:
                request.result = final_result
                session.commit()
                logger.debug(f"Updated request {insert_id} result to '{final_result}'")
            else:
                logger.warning(f"Request {insert_id} not found for finalization")

        except Exception as e:
            logger.error(f"Failed to finalize request {insert_id}: {e}")
            if session:
                session.rollback()

        finally:
            if session:
                try:
                    session.close()
                except Exception as e:
                    logger.error(f"Session cleanup failed in finalize: {e}")

    except Exception as e:
        logger.error(f"Error in update_finalize hook: {e}")
        # Never raise - we're in the finally block and must not crash the worker
