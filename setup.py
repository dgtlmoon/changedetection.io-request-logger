#!/usr/bin/env python
from setuptools import setup, find_packages
import os

here = os.path.abspath(os.path.dirname(__file__))

# Read README for long description
with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='changedetection.io-request-logger',
    version='0.1.0',
    description='Database request logging plugin for changedetection.io (MySQL/PostgreSQL/SQLite)',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Your Name',
    author_email='you@example.com',
    url='https://github.com/yourusername/changedetection.io-request-logger',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'changedetection_request_logger': ['../schema.sql'],
    },
    install_requires=[
        'changedetection.io>=0.53.4',  # Requires update_handler_alter and update_finalize hooks
        'SQLAlchemy>=2.0.0',           # ORM and database abstraction
        'alembic>=1.13.0',             # Database migrations
        'PyMySQL>=1.1.0',              # MySQL driver
        'psycopg2-binary>=2.9.0',      # PostgreSQL driver (optional)
        'brotli>=1.0.0',               # Compression
    ],
    # CRITICAL: Register the plugin via entry_points
    entry_points={
        'changedetectionio': [
            'request_logger = changedetection_request_logger.plugin_orm',
        ],
    },
    python_requires='>=3.10',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Topic :: Internet :: WWW/HTTP :: Site Management',
        'Topic :: System :: Monitoring',
        'Topic :: Database',
    ],
)
