from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock


class GameDatabase:
    """负责游戏基础数据的 SQLite 持久化。"""

    def __init__(self, db_path: Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parents[2]
        default_db_path = base_dir / "data" / "game_lens.db"
        self._db_path = db_path or default_db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON;")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_name TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name_original TEXT NOT NULL,
                    name_translated TEXT NOT NULL,
                    game_id INTEGER NOT NULL,
                    gender TEXT,
                    extra_info TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(name_original, game_id),
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );
                """
            )
            self._connection.commit()

    def add_game(self, game_name: str) -> int:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")

        with self._lock:
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

        with self._lock:
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
        with self._lock:
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

        with self._lock:
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

        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM games WHERE game_name = ?;",
                (normalized_name,),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def add_character(
        self,
        name_original: str,
        name_translated: str,
        game_id: int,
        gender: str | None = None,
        extra_info: str | None = None,
    ) -> int:
        normalized_name_original = name_original.strip()
        normalized_name_translated = name_translated.strip()
        normalized_gender = gender.strip() if isinstance(gender, str) else ""
        normalized_extra_info = extra_info.strip() if isinstance(extra_info, str) else ""
        if not normalized_name_original:
            raise ValueError("原文名称不能为空")
        if not normalized_name_translated:
            raise ValueError("译文名称不能为空")
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")

        with self._lock:
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO characters (
                        name_original,
                        name_translated,
                        game_id,
                        gender,
                        extra_info
                    ) VALUES (?, ?, ?, ?, ?);
                    """,
                    (
                        normalized_name_original,
                        normalized_name_translated,
                        game_id,
                        normalized_gender or None,
                        normalized_extra_info or None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"角色“{normalized_name_original}”在该游戏中已存在"
                ) from exc

            self._connection.commit()
            return int(cursor.lastrowid)

    def character_exists(self, name_original: str, game_id: int) -> bool:
        normalized_name_original = name_original.strip()
        if not normalized_name_original:
            raise ValueError("原文名称不能为空")
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT 1
                FROM characters
                WHERE name_original = ? AND game_id = ?
                LIMIT 1;
                """,
                (normalized_name_original, game_id),
            )
            row = cursor.fetchone()
            return row is not None

    def list_characters_by_game(self, game_id: int) -> list[dict[str, object]]:
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    id,
                    name_original,
                    name_translated,
                    game_id,
                    gender,
                    extra_info,
                    created_at,
                    updated_at
                FROM characters
                WHERE game_id = ?
                ORDER BY id ASC;
                """,
                (game_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "name_original": row["name_original"],
                "name_translated": row["name_translated"],
                "game_id": row["game_id"],
                "gender": row["gender"],
                "extra_info": row["extra_info"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_all_characters_with_game_name(self) -> list[dict[str, object]]:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    c.id,
                    c.name_original,
                    c.name_translated,
                    c.game_id,
                    g.game_name,
                    c.gender,
                    c.extra_info,
                    c.created_at,
                    c.updated_at
                FROM characters AS c
                LEFT JOIN games AS g ON c.game_id = g.id
                ORDER BY c.id ASC;
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "name_original": row["name_original"],
                "name_translated": row["name_translated"],
                "game_id": row["game_id"],
                "game_name": row["game_name"],
                "gender": row["gender"],
                "extra_info": row["extra_info"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def update_character(
        self,
        character_id: int,
        name_translated: str,
        gender: str | None = None,
        extra_info: str | None = None,
    ) -> bool:
        normalized_name_translated = name_translated.strip()
        normalized_gender = gender.strip() if isinstance(gender, str) else ""
        normalized_extra_info = extra_info.strip() if isinstance(extra_info, str) else ""
        if character_id <= 0:
            raise ValueError("character_id 必须为正整数")
        if not normalized_name_translated:
            raise ValueError("译文名称不能为空")

        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE characters
                SET
                    name_translated = ?,
                    gender = ?,
                    extra_info = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (
                    normalized_name_translated,
                    normalized_gender or None,
                    normalized_extra_info or None,
                    character_id,
                ),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def delete_character(self, character_id: int) -> bool:
        if character_id <= 0:
            raise ValueError("character_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM characters WHERE id = ?;",
                (character_id,),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()
