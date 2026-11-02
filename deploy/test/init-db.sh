#!/bin/bash
set -eu
export ODOO_DB_PASSWORD="$(cat /run/secrets/odoo_db_password)"
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<'SQL'
\getenv app_password ODOO_DB_PASSWORD
CREATE ROLE odoo LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
CREATE DATABASE odoo20 OWNER odoo ENCODING 'UTF8' TEMPLATE template0;
SQL
unset ODOO_DB_PASSWORD
