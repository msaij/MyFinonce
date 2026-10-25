#!/bin/sh
# Creates or updates the role the backend logs in as, from the values in .env.
#
# Postgres runs this by itself the first time it starts on an empty volume. On an existing
# database, `scripts\dev.ps1 db-role` runs it: after a change to .env, or to rotate a
# password. Every step is safe to repeat.
#
# The app logs in as APP_DB_USER, not as the superuser. A superuser can read and write any
# file on the server and run shell commands (COPY ... PROGRAM), so one SQL injection would
# have been the whole container. The app role owns the app's tables (it runs the migrations
# at startup) and may create databases (the test suite makes its own), and nothing more.
#
# A subshell, because the image's entrypoint sources a script that is not executable: `set`
# and a failure stay in here, and the failure still stops the first start.
(
set -eu
: "${APP_DB_USER:?set APP_DB_USER in .env}" "${APP_DB_PASSWORD:?set APP_DB_PASSWORD in .env}"

run() {
  psql -v ON_ERROR_STOP=1 --no-psqlrc -q -At --username "$POSTGRES_USER" --dbname "$1" \
    -v app="$APP_DB_USER" -v app_pw="$APP_DB_PASSWORD" -v su_pw="${POSTGRES_PASSWORD:-}"
}

run postgres <<'SQL'
SELECT format('CREATE ROLE %I', :'app') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app')
\gexec
SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION NOBYPASSRLS PASSWORD %L', :'app', :'app_pw')
\gexec
-- pg_prewarm sits in `extensions` in the test databases (see template1 below).
SELECT format('ALTER ROLE %I SET search_path = "$user", public, extensions', :'app')
\gexec
-- The superuser's password as well, so .env stays the one place either is written down.
SELECT format('ALTER ROLE %I PASSWORD %L', current_user, :'su_pw') WHERE :'su_pw' <> ''
\gexec
SQL

run "${POSTGRES_DB:-mutual_funds}" <<'SQL'
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', current_database(), :'app')
\gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app')
\gexec
-- Only a superuser can create it. The summary rebuild asks for it inside its transaction,
-- where a refusal would roll the whole rebuild back.
CREATE EXTENSION IF NOT EXISTS pg_prewarm;
-- Every table (its indexes and column sequences go with it) and every sequence of its own
-- moves to the app role. Ownership is catalog-only: no row is read or rewritten.
SELECT format('ALTER %s public.%I OWNER TO %I',
              CASE c.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
              c.relname, :'app')
FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
  AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = :'app')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d
                  WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('e', 'a', 'i'))
\gexec
SQL

# The test suite copies its databases from template1 and starts each test with
# DROP SCHEMA public CASCADE, which would take an extension kept in public with it.
run template1 <<'SQL'
CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO PUBLIC;
CREATE EXTENSION IF NOT EXISTS pg_prewarm SCHEMA extensions;
SQL
)
