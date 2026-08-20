from __future__ import annotations

import sqlite3
from pathlib import Path


class GameDatabase:
    """负责游戏基础数据的 SQLite 持久化。"""

    def __init__(self, db_path: Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parents[2]
        default_db_path = base_dir / "data" / "game_lens.db"
        self._db_path = db_path or default_db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(self._db_path)
        self._connection.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._connection.commit()

    def add_game(self, game_name: str) -> int:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")

        try:
            cursor = self._connection.execute(
                "INSERT INTO games (game_name) VALUES (?);",
                (normalized_name,),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"游戏“{normalized_name}”已存在") from exc

        self._connection.commit()
        return int(cursor.lastrowid)

    def get_game_by_name(self, game_name: str) -> dict[str, object] | None:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")

        cursor = self._connection.execute(
            """
            SELECT id, game_name, created_at
            FROM games
            WHERE game_name = ?;
            """,
            (normalized_name,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return {
            "id": row["id"],
            "game_name": row["game_name"],
            "created_at": row["created_at"],
        }

    def list_games(self) -> list[dict[str, object]]:
        cursor = self._connection.execute(
            """
            SELECT id, game_name, created_at
            FROM games
            ORDER BY game_name COLLATE NOCASE ASC;
            """
        )
        rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "game_name": row["game_name"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def update_game_name(self, old_game_name: str, new_game_name: str) -> bool:
        normalized_old_name = old_game_name.strip()
        normalized_new_name = new_game_name.strip()
        if not normalized_old_name or not normalized_new_name:
            raise ValueError("游戏名称不能为空")

        try:
            cursor = self._connection.execute(
                """
                UPDATE games
                SET game_name = ?
                WHERE game_name = ?;
                """,
                (normalized_new_name, normalized_old_name),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"游戏“{normalized_new_name}”已存在") from exc

        self._connection.commit()
        return cursor.rowcount > 0

    def delete_game(self, game_name: str) -> int:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")

        cursor = self._connection.execute(
            "DELETE FROM games WHERE game_name = ?;",
            (normalized_name,),
        )
        self._connection.commit()
        return int(cursor.rowcount)

    def close(self) -> None:
        self._connection.close()
