"""Database access for the core app.

Core owns no concrete models. This module holds infrastructure level queries
so that, like every other app, nothing outside this file talks to the
database directly.
"""

from django.db import DatabaseError, connection


def database_is_available() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return False
    return True
