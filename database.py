"""Keep local SQLite and hosted Postgres behind the same query interface."""

import os
import re
import sqlite3
from contextlib import contextmanager


class Postgres:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        if query == "BEGIN IMMEDIATE":
            # Serialize account creation and single-use link redemption, as SQLite does.
            return self.connection.execute("SELECT pg_advisory_xact_lock(73481902)")
        return self.connection.execute(self.query(query), parameters)

    @staticmethod
    def query(query):
        # Qualify tables because transaction poolers can switch database sessions.
        return re.sub(
            r"\b(users|user_profiles|boards|designs|board_designs|sessions|oauth_attempts|oauth_identities|email_links)\b",
            r"pluck.\1",
            query.replace("?", "%s"),
        )

    def executemany(self, query, parameters):
        with self.connection.cursor() as cursor:
            cursor.executemany(self.query(query), parameters)

    def commit(self):
        self.connection.commit()


@contextmanager
def connect(local_path):
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            url, row_factory=dict_row, prepare_threshold=None,
            sslmode="require", connect_timeout=15,
        ) as connection:
            yield Postgres(connection)
    else:
        connection = sqlite3.connect(local_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def initialize_postgres(connection, auth_schema):
    connection.execute("CREATE SCHEMA IF NOT EXISTS pluck")
    statements = """
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            salt TEXT NOT NULL, password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS boards(
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            owner_id TEXT REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS designs(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, image TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '', creator TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            rowid BIGINT GENERATED ALWAYS AS IDENTITY,
            width INTEGER, height INTEGER, url TEXT, thumbnail_url TEXT,
            owner_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            creator_avatar_url TEXT
        );
        CREATE TABLE IF NOT EXISTS board_designs(
            board_id TEXT REFERENCES boards(id) ON DELETE CASCADE,
            design_id TEXT REFERENCES designs(id) ON DELETE CASCADE,
            PRIMARY KEY(board_id, design_id)
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            expires DOUBLE PRECISION NOT NULL
        );
        CREATE INDEX IF NOT EXISTS designs_owner ON designs(owner_id);
        CREATE INDEX IF NOT EXISTS boards_owner ON boards(owner_id);
    """ + auth_schema.replace("REAL", "DOUBLE PRECISION")
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(statement)
