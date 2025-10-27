import sqlite3

class Database:
    def __init__(self, db_file='db/database.db'):
        self._db = sqlite3.connect(db_file)

    def __enter__(self):
        return self._db.cursor()

    def __exit__(self, type, value, tb):
        if tb is None:
            self._db.commit()
        else:
            self._db.rollback()
