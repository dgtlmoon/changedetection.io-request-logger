# changedetection.io MySQL Logger

High-performance request logging plugin for [changedetection.io](https://github.com/dgtlmoon/changedetection.io).

- ✅ Works with **MySQL, PostgreSQL, or SQLite**
- ✅ **83% storage savings** with normalized schema
- ✅ **Auto-creates tables** - no manual SQL needed
- ✅ **Automatic migrations** when schema changes
- ✅ Optimized for 10M+ rows

## Requirements

- changedetection.io >= 0.53.4
- MySQL 5.7+ / PostgreSQL 10+ / SQLite 3
- Python 3.10+

## Quick Start

### 1. Install Plugin

**For Docker (docker-compose.yml):**

Add to your `docker-compose.yml`:
```yaml
services:
  changedetection:
    environment:
      # Install the logger plugin from PyPI
      - EXTRA_PACKAGES=changedetection.io-mysql-logger

      # Configure logger database
      - LOGGER_DB_TYPE=mysql
      - LOGGER_MYSQL_HOST=cdio_mysql_logger
      - LOGGER_MYSQL_USER=changedetection
      - LOGGER_MYSQL_PASSWORD=your_secure_password
      - LOGGER_MYSQL_DATABASE=changedetection_logs
```

**For Local Install:**
```bash
cd changedetection.io-mysql-logger
pip install -e .
```

### 2. Configure Database

**MySQL:**
```bash
export LOGGER_DB_TYPE=mysql
export LOGGER_MYSQL_HOST=localhost
export LOGGER_MYSQL_USER=changedetection
export LOGGER_MYSQL_PASSWORD=your_secure_password
export LOGGER_MYSQL_DATABASE=changedetection_logs
export HOSTNAME=$(hostname)
```

**PostgreSQL:**
```bash
export LOGGER_DB_TYPE=postgresql
export LOGGER_POSTGRES_HOST=localhost
export LOGGER_POSTGRES_USER=changedetection
export LOGGER_POSTGRES_PASSWORD=your_secure_password
export LOGGER_POSTGRES_DB=changedetection_logs
export HOSTNAME=$(hostname)
```

**SQLite (testing):**
```bash
export LOGGER_DB_TYPE=sqlite
export LOGGER_SQLITE_PATH=/tmp/changedetection_logs.db
export HOSTNAME=$(hostname)
```

### 3. Create Database (MySQL/PostgreSQL only)

```bash
# MySQL
mysql -u root -p -e "CREATE DATABASE changedetection_logs CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# PostgreSQL
createdb -U postgres changedetection_logs
```

### 4. Run changedetection.io

**Docker:**
```bash
docker-compose up -d
```

**Local:**
```bash
python changedetection.py
```

**That's it!** Tables are created automatically on first request.

## Docker Complete Example

```yaml
version: '3'

services:
  changedetection:
    image: ghcr.io/dgtlmoon/changedetection.io:latest
    container_name: changedetection
    hostname: changedetection
    volumes:
      - ./datastore:/datastore
    environment:
      # Install logger plugin
      - EXTRA_PACKAGES=changedetection.io-mysql-logger

      # Logger database config
      - LOGGER_DB_TYPE=mysql
      - LOGGER_MYSQL_HOST=cdio_mysql_logger
      - LOGGER_MYSQL_USER=changedetection
      - LOGGER_MYSQL_PASSWORD=secure_password_here
      - LOGGER_MYSQL_DATABASE=changedetection_logs
      - LOGGER_DB_POOL_SIZE=5
    ports:
      - "5000:5000"
    depends_on:
      - cdio_mysql_logger
    restart: unless-stopped

  cdio_mysql_logger:
    image: mysql:8.0
    container_name: cdio_mysql_logger
    hostname: cdio_mysql_logger
    environment:
      - MYSQL_ROOT_PASSWORD=root_password_here
      - MYSQL_DATABASE=changedetection_logs
      - MYSQL_USER=changedetection
      - MYSQL_PASSWORD=secure_password_here
    volumes:
      - cdio_mysql_logger_data:/var/lib/mysql
    restart: unless-stopped

volumes:
  cdio_mysql_logger_data:
```

Then run:
```bash
docker-compose up -d
```

## What Gets Logged

Each watch check logs:
- ✅ Timestamp with milliseconds
- ✅ Watch UUID and URL
- ✅ Hostname (server running the check)
- ✅ Proxy key and endpoint
- ✅ Browser connection URL (CDP/Selenium endpoint)
- ✅ Duration in milliseconds
- ✅ HTTP status code and content length
- ✅ Browser steps (brotli compressed)
- ✅ Result status (success/failed)
- ✅ Error type and message

## Storage Efficiency

**With normalized schema:**

| Rows | Storage |
|------|---------|
| 1M | 170 MB |
| 10M | 1.7 GB |
| 100M | 17 GB |

**vs. denormalized:**
| Rows | Storage |
|------|---------|
| 1M | 1.0 GB |
| 10M | 10 GB |
| 100M | 100 GB |

**Savings: 83%** by using lookup tables for repeated strings.

## Example Queries

### Recent requests with all details

```sql
SELECT
    r.request_timestamp,
    h.hostname,
    w.watch_url,
    p.proxy_key,
    b.browser_connection_url,
    r.duration_ms,
    r.result
FROM watch_requests r
JOIN hostnames h ON r.hostname_id = h.id
JOIN watches w ON r.watch_id = w.id
LEFT JOIN proxy_endpoints p ON r.proxy_id = p.id
LEFT JOIN browser_connections b ON r.browser_conn_id = b.id
WHERE r.request_date = CURDATE()
ORDER BY r.request_timestamp DESC
LIMIT 100;
```

### Proxy performance

```sql
SELECT
    p.proxy_key,
    COUNT(*) as requests,
    AVG(r.duration_ms) as avg_ms,
    SUM(CASE WHEN r.result = 'failed' THEN 1 ELSE 0 END) as failures
FROM watch_requests r
JOIN proxy_endpoints p ON r.proxy_id = p.id
WHERE r.request_date >= CURDATE() - INTERVAL 7 DAY
GROUP BY p.proxy_key
ORDER BY avg_ms;
```

### Error summary

```sql
SELECT
    e.error_type,
    COUNT(*) as occurrences,
    h.hostname
FROM watch_requests r
JOIN error_types e ON r.error_type_id = e.id
JOIN hostnames h ON r.hostname_id = h.id
WHERE r.request_date >= CURDATE() - INTERVAL 1 DAY
GROUP BY e.error_type, h.hostname
ORDER BY occurrences DESC;
```

## Schema Updates

When you need to add/modify columns:

```bash
# 1. Edit models.py (add your field)

# 2. Generate migration
alembic revision --autogenerate -m "Add new field"

# 3. Apply migration
alembic upgrade head
```

Done! All existing data is preserved.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LOGGER_DB_TYPE` | mysql | Database type: mysql, postgresql, sqlite |
| `LOGGER_MYSQL_HOST` | localhost | MySQL server |
| `LOGGER_MYSQL_USER` | changedetection | MySQL username |
| `LOGGER_MYSQL_PASSWORD` | *(required)* | MySQL password |
| `LOGGER_MYSQL_DATABASE` | changedetection_logs | Database name |
| `LOGGER_DB_POOL_SIZE` | 5 | Connection pool size |
| `HOSTNAME` | *(auto)* | Server hostname for logging |

## Switching Databases

Change environment variables and restart:

```bash
# From MySQL to PostgreSQL
export LOGGER_DB_TYPE=postgresql
export LOGGER_POSTGRES_PASSWORD=your_password

# Tables auto-create on next run
python changedetection.py
```

## Database Schema

### Main Table
`watch_requests` - High-volume log entries

### Lookup Tables (eliminate duplicate strings)
- `hostnames` - Server hostnames
- `proxy_endpoints` - Proxy configurations
- `browser_connections` - Browser endpoints
- `watches` - Watch configurations
- `error_types` - Error classifications

Foreign keys link main table to lookups = 83% storage savings!

## Troubleshooting

**Plugin not loading?**
```python
from changedetectionio.pluggy_interface import plugin_manager
print([name for name, _ in plugin_manager.list_name_plugin()])
# Should show: mysql_logger
```

**Test database connection:**
```python
from changedetection_mysql_logger.plugin_orm import get_database_url
print(get_database_url())
```

**View tables:**
```bash
# MySQL
mysql -u changedetection -p changedetection_logs -e "SHOW TABLES;"

# PostgreSQL
psql -U changedetection -d changedetection_logs -c "\dt"

# SQLite
sqlite3 /tmp/changedetection_logs.db ".tables"
```

**Check logs:**
```bash
# Database operations never block - failures logged with logger.critical()
tail -f /var/log/changedetection.log | grep -i "mysql\|sqlalchemy\|logger_"
```

## License

MIT License
