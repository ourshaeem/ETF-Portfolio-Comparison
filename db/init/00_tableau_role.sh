#!/bin/bash
# Runs once, on first boot of the Postgres container. Creates the read-only login
# Tableau Desktop uses for its live connection. Tables/views are created later by
# the pipeline (db/schema.sql + db/views.sql), so default privileges cover them.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tableau_reader') THEN
            CREATE ROLE tableau_reader LOGIN PASSWORD '${TABLEAU_DB_PASSWORD:-tableau}';
        END IF;
    END
    \$\$;
    GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO tableau_reader;
    GRANT USAGE ON SCHEMA public TO tableau_reader;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO tableau_reader;
EOSQL
