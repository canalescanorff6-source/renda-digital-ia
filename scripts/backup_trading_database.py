#!/usr/bin/env python3
"""Create and verify a standalone SQLite backup without replacing any file."""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from time import monotonic


def backup_database(source: Path, destination: Path):
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("A origem precisa ser um arquivo SQLite existente e não vazio.")

    # Reserve a new private file. An existing destination always wins.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    deadline = monotonic() + 60

    def check_deadline(status, remaining, total):
        if monotonic() >= deadline and remaining:
            raise TimeoutError("O SQLite não concluiu a cópia no prazo.")

    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live:
            with closing(sqlite3.connect(destination)) as copy:
                live.backup(copy, pages=256, progress=check_deadline)
                if copy.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise sqlite3.DatabaseError("Não foi possível gerar uma cópia autônoma.")
                if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise sqlite3.DatabaseError("A cópia falhou na verificação de integridade.")
    except BaseException:
        destination.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Caminho real do SQLite em uso")
    parser.add_argument("destination", type=Path, help="Arquivo novo em local privado")
    args = parser.parse_args()
    try:
        backup_database(args.source, args.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Backup não concluído: {type(exc).__name__}\n")
    print("Cópia SQLite criada. Verificação de integridade: OK.")


if __name__ == "__main__":
    main()
