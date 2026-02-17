"""
SQLAlchemy models for changedetection.io request logging.

Supports MySQL, PostgreSQL, SQLite via SQLAlchemy.
Schema migrations managed by Alembic.
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime,
    Date, ForeignKey, Index, LargeBinary
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import hashlib

Base = declarative_base()


class Hostname(Base):
    """Lookup table for server hostnames (typically 1-10 unique values)"""
    __tablename__ = 'hostnames'

    id = Column(Integer, primary_key=True)
    hostname = Column(String(255), nullable=True, unique=True, index=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    requests = relationship('WatchRequest', back_populates='hostname_obj')


class ProxyEndpoint(Base):
    """Lookup table for proxy configurations (typically 5-50 unique values)"""
    __tablename__ = 'proxy_endpoints'

    id = Column(Integer, primary_key=True)
    proxy_key = Column(String(128), index=True, comment='Proxy name/region (e.g., europe-frankfurt)')
    proxy_endpoint = Column(String(512), nullable=False, comment='Proxy URL (e.g., socks5://10.9.0.12:1080)')
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    request_count = Column(Integer, default=0, comment='Total requests using this proxy')

    # Relationships
    requests = relationship('WatchRequest', back_populates='proxy_obj')

    __table_args__ = (
        Index('uk_proxy', 'proxy_key', 'proxy_endpoint', unique=True),
    )


class BrowserConnection(Base):
    """Lookup table for browser connection endpoints (typically 1-20 unique values)"""
    __tablename__ = 'browser_connections'

    id = Column(Integer, primary_key=True)
    browser_connection_url = Column(String(512), nullable=False, comment='CDP/WS endpoint or Selenium hub')
    fetch_backend = Column(String(64), nullable=False, index=True, comment='html_webdriver, html_playwright, etc')
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    request_count = Column(Integer, default=0, comment='Total requests using this connection')

    # Relationships
    requests = relationship('WatchRequest', back_populates='browser_conn_obj')

    __table_args__ = (
        Index('uk_browser_conn', 'browser_connection_url', 'fetch_backend', unique=True),
    )


class Watch(Base):
    """Lookup table for watches - tracks URL changes via hash of (uuid + url)"""
    __tablename__ = 'watches'

    id = Column(Integer, primary_key=True)
    watch_uuid = Column(String(36), nullable=False, index=True, comment='Watch UUID (not unique - can have multiple URLs)')
    watch_url = Column(String(2048), nullable=False, index=True, comment='URL at time of request')
    url_hash = Column(String(32), nullable=False, unique=True, index=True, comment='MD5(watch_uuid + watch_url) - ensures uniqueness')
    processor = Column(String(64))
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    request_count = Column(Integer, default=0, comment='Total requests for this watch+URL combination')

    # Relationships
    requests = relationship('WatchRequest', back_populates='watch_obj')


class ErrorType(Base):
    """Lookup table for error types (typically 20-50 unique values)"""
    __tablename__ = 'error_types'

    id = Column(Integer, primary_key=True)
    error_type = Column(String(128), nullable=False, unique=True, index=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    occurrence_count = Column(Integer, default=0, comment='Total occurrences of this error')

    # Relationships
    requests = relationship('WatchRequest', back_populates='error_type_obj')


class WatchRequest(Base):
    """Main request log table - normalized with foreign keys to lookup tables"""
    __tablename__ = 'watch_requests'

    # Primary key
    id = Column(Integer, primary_key=True)

    # Core identifiers (foreign keys)
    app_guid = Column(String(64), nullable=False, index=True, comment='Application instance GUID')
    hostname_id = Column(Integer, ForeignKey('hostnames.id'), nullable=False, index=True)
    watch_id = Column(Integer, ForeignKey('watches.id'), nullable=False, index=True)

    # Temporal data (for partitioning in MySQL)
    request_date = Column(Date, nullable=False, index=True, comment='Request date for partitioning')
    request_timestamp = Column(DateTime(timezone=False), nullable=False, index=True,
                               comment='Precise timestamp with milliseconds')

    # Network identifiers (foreign keys)
    proxy_id = Column(Integer, ForeignKey('proxy_endpoints.id'), index=True)
    browser_conn_id = Column(Integer, ForeignKey('browser_connections.id'), index=True)

    # Browser steps (variable, not normalized)
    browser_steps = Column(LargeBinary, comment='Brotli-compressed base64 browser steps JSON')
    browser_steps_count = Column(Integer, default=0)

    # Status tracking
    result = Column(String(255), index=True, comment='success, failed, timeout, etc')

    # Performance metrics
    duration_ms = Column(Integer)
    content_length = Column(Integer)
    status_code = Column(Integer)

    # Error tracking
    error_type_id = Column(Integer, ForeignKey('error_types.id'), index=True)
    error_message = Column(Text, comment='Error details (variable, not normalized)')

    # Relationships
    hostname_obj = relationship('Hostname', back_populates='requests')
    watch_obj = relationship('Watch', back_populates='requests')
    proxy_obj = relationship('ProxyEndpoint', back_populates='requests')
    browser_conn_obj = relationship('BrowserConnection', back_populates='requests')
    error_type_obj = relationship('ErrorType', back_populates='requests')

    # Composite indexes for common query patterns
    __table_args__ = (
        Index('idx_date_app', 'request_date', 'app_guid', 'request_timestamp'),
        Index('idx_watch_date', 'watch_id', 'request_date'),
        Index('idx_hostname_date', 'hostname_id', 'request_date'),
        Index('idx_proxy_date', 'proxy_id', 'request_date'),
        Index('idx_analytics', 'request_date', 'app_guid', 'hostname_id', 'result', 'duration_ms'),
    )


# Helper functions for upsert operations

def get_or_create_hostname(session, hostname):
    """Get or create hostname entry.

    Args:
        session: SQLAlchemy session
        hostname: Hostname string

    Returns:
        Hostname object
    """
    obj = session.query(Hostname).filter_by(hostname=hostname).first()
    if not obj:
        obj = Hostname(hostname=hostname)
        session.add(obj)
        session.flush()  # Get the ID without committing
    else:
        obj.last_seen = datetime.now()
    return obj


def get_or_create_proxy(session, proxy_key, proxy_endpoint):
    """Get or create proxy endpoint entry.

    Args:
        session: SQLAlchemy session
        proxy_key: Proxy key/name (can be None)
        proxy_endpoint: Proxy URL

    Returns:
        ProxyEndpoint object or None
    """
    if not proxy_endpoint:
        return None

    obj = session.query(ProxyEndpoint).filter_by(
        proxy_key=proxy_key,
        proxy_endpoint=proxy_endpoint
    ).first()

    if not obj:
        obj = ProxyEndpoint(
            proxy_key=proxy_key,
            proxy_endpoint=proxy_endpoint,
            request_count=1
        )
        session.add(obj)
        session.flush()
    else:
        obj.last_seen = datetime.now()
        obj.request_count += 1

    return obj


def get_or_create_browser_conn(session, browser_url, fetch_backend):
    """Get or create browser connection entry.

    Args:
        session: SQLAlchemy session
        browser_url: Browser connection URL
        fetch_backend: Fetch backend type

    Returns:
        BrowserConnection object or None
    """
    if not browser_url:
        return None

    obj = session.query(BrowserConnection).filter_by(
        browser_connection_url=browser_url,
        fetch_backend=fetch_backend
    ).first()

    if not obj:
        obj = BrowserConnection(
            browser_connection_url=browser_url,
            fetch_backend=fetch_backend,
            request_count=1
        )
        session.add(obj)
        session.flush()
    else:
        obj.last_seen = datetime.now()
        obj.request_count += 1

    return obj


def get_or_create_watch(session, watch_uuid, watch_url, processor):
    """Get or create watch entry based on hash of (uuid + url).

    When a watch changes URL, a new record is created - preserving history.

    Args:
        session: SQLAlchemy session
        watch_uuid: Watch UUID
        watch_url: Watch URL
        processor: Processor type

    Returns:
        Watch object
    """
    # Calculate MD5 hash of (watch_uuid + watch_url) for uniqueness
    url_hash = hashlib.md5(f"{watch_uuid}{watch_url}".encode('utf-8')).hexdigest()

    # Query by hash - if URL changes, this will not find existing record
    obj = session.query(Watch).filter_by(url_hash=url_hash).first()

    if not obj:
        # New watch or URL changed - create new record
        obj = Watch(
            watch_uuid=watch_uuid,
            watch_url=watch_url,
            url_hash=url_hash,
            processor=processor,
            request_count=1
        )
        session.add(obj)
        session.flush()
    else:
        # Same watch+URL - update existing record
        obj.last_seen = datetime.now()
        obj.processor = processor
        obj.request_count += 1

    return obj


def get_or_create_error_type(session, error_type):
    """Get or create error type entry.

    Args:
        session: SQLAlchemy session
        error_type: Error type string

    Returns:
        ErrorType object or None
    """
    if not error_type:
        return None

    obj = session.query(ErrorType).filter_by(error_type=error_type).first()

    if not obj:
        obj = ErrorType(
            error_type=error_type,
            occurrence_count=1
        )
        session.add(obj)
        session.flush()
    else:
        obj.last_seen = datetime.now()
        obj.occurrence_count += 1

    return obj
