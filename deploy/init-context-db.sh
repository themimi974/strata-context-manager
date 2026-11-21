#!/bin/bash
# Runs once, on the first start of an empty Postgres volume: the Context Manager's own role and database.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -v pw="$CTX_DB_PASSWORD" <<'SQL'
CREATE ROLE context LOGIN PASSWORD :'pw';
CREATE DATABASE context OWNER context;
SQL
