import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.backup_trading_database import backup_database


class TradingDatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "original.db"
        self.destination = Path(directory.name) / "backup.db"

    def test_copies_committed_wal_records_and_ignores_uncommitted_records(self):
        with closing(sqlite3.connect(self.source)) as live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("CREATE TABLE users (email TEXT)")
            live.execute("INSERT INTO users VALUES ('real@example.test')")
            live.commit()
            live.execute("INSERT INTO users VALUES ('uncommitted@example.test')")
            backup_database(self.source, self.destination)
            with closing(sqlite3.connect(self.destination)) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                self.assertEqual(backup.execute("SELECT email FROM users").fetchall(),
                                 [("real@example.test",)])

    def test_missing_source_does_not_create_either_file(self):
        with self.assertRaises(FileNotFoundError):
            backup_database(self.source, self.destination)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_existing_destination_is_preserved(self):
        with closing(sqlite3.connect(self.source)) as live:
            live.execute("CREATE TABLE users (email TEXT)")
        self.destination.write_bytes(b"previous backup")
        with self.assertRaises(FileExistsError):
            backup_database(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"previous backup")

    def test_invalid_source_leaves_no_backup(self):
        self.source.write_bytes(b"not a sqlite database")
        with self.assertRaises(sqlite3.DatabaseError):
            backup_database(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_empty_source_is_not_accepted_as_a_backup(self):
        self.source.touch()
        with self.assertRaises(ValueError):
            backup_database(self.source, self.destination)
        self.assertFalse(self.destination.exists())
