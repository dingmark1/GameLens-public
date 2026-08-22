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
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dialogues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_name_original TEXT,
                    dialog_text_original TEXT NOT NULL,
                    dialog_text_translated TEXT,
                    game_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                    FOREIGN KEY (character_name_original, game_id)
                        REFERENCES characters(name_original, game_id)
                        ON DELETE SET NULL
                );
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    start_conversation_id INTEGER,
                    end_conversation_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS game_intros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_name TEXT NOT NULL UNIQUE,
                    game_intro TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_name)
                        REFERENCES games(game_name)
                        ON DELETE CASCADE
                        ON UPDATE CASCADE
                );
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_summaries_game_id
                ON summaries(game_id);
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_summaries_created_at
                ON summaries(created_at);
                """
            )
            self._migrate_dialogues_table_if_needed()
            self._ensure_dialogues_character_delete_trigger()
            self._connection.commit()

    def _ensure_dialogues_character_delete_trigger(self) -> None:
        self._connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_dialogues_character_set_null_before_delete
            BEFORE DELETE ON characters
            FOR EACH ROW
            BEGIN
                UPDATE dialogues
                SET character_name_original = NULL
                WHERE character_name_original = OLD.name_original
                    AND game_id = OLD.game_id;
            END;
            """
        )

    def _migrate_dialogues_table_if_needed(self) -> None:
        cursor = self._connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'dialogues';
            """
        )
        if cursor.fetchone() is None:
            return

        if not self._dialogues_table_needs_migration():
            return

        self._connection.execute(
            "DROP TRIGGER IF EXISTS trg_dialogues_character_set_null_before_delete;"
        )
        self._connection.execute("ALTER TABLE dialogues RENAME TO dialogues_old;")
        self._connection.execute(
            """
            CREATE TABLE dialogues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name_original TEXT,
                dialog_text_original TEXT NOT NULL,
                dialog_text_translated TEXT,
                game_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                FOREIGN KEY (character_name_original, game_id)
                    REFERENCES characters(name_original, game_id)
                    ON DELETE SET NULL
            );
            """
        )
        self._connection.execute(
            """
            INSERT INTO dialogues (
                id,
                character_name_original,
                dialog_text_original,
                dialog_text_translated,
                game_id,
                created_at
            )
            SELECT
                d.id,
                CASE
                    WHEN d.character_name_original IS NULL
                        OR TRIM(d.character_name_original) = '' THEN NULL
                    WHEN EXISTS (
                        SELECT 1
                        FROM characters AS c
                        WHERE c.name_original = d.character_name_original
                            AND c.game_id = d.game_id
                    ) THEN d.character_name_original
                    ELSE NULL
                END AS character_name_original,
                COALESCE(d.dialog_text_original, ''),
                COALESCE(d.dialog_text_translated, ''),
                d.game_id,
                COALESCE(d.created_at, CURRENT_TIMESTAMP)
            FROM dialogues_old AS d;
            """
        )
        self._connection.execute("DROP TABLE dialogues_old;")

    def _dialogues_table_needs_migration(self) -> bool:
        column_rows = self._connection.execute("PRAGMA table_info(dialogues);").fetchall()
        column_names = {str(row["name"]) for row in column_rows}
        expected_column_names = {
            "id",
            "character_name_original",
            "dialog_text_original",
            "dialog_text_translated",
            "game_id",
            "created_at",
        }
        if not expected_column_names.issubset(column_names):
            return True

        foreign_key_rows = self._connection.execute(
            "PRAGMA foreign_key_list(dialogues);"
        ).fetchall()
        has_game_fk = any(
            row["table"] == "games"
            and row["from"] == "game_id"
            and row["to"] == "id"
            and str(row["on_delete"]).upper() == "CASCADE"
            for row in foreign_key_rows
        )
        if not has_game_fk:
            return True

        character_fk_rows = [
            row
            for row in foreign_key_rows
            if row["table"] == "characters"
            and str(row["on_delete"]).upper() == "SET NULL"
        ]
        character_fk_pairs = {(row["from"], row["to"]) for row in character_fk_rows}
        return not {
            ("character_name_original", "name_original"),
            ("game_id", "game_id"),
        }.issubset(character_fk_pairs)

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

    def add_game_intro(self, game_name: str, game_intro: str) -> int:
        normalized_name = game_name.strip()
        normalized_intro = game_intro.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")
        if not normalized_intro:
            raise ValueError("游戏简介不能为空")

        with self._lock:
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO game_intros (
                        game_name,
                        game_intro
                    ) VALUES (?, ?);
                    """,
                    (normalized_name, normalized_intro),
                )
            except sqlite3.IntegrityError as exc:
                game_exists = self._connection.execute(
                    "SELECT 1 FROM games WHERE game_name = ?;",
                    (normalized_name,),
                ).fetchone()
                if game_exists is None:
                    raise ValueError(f"游戏“{normalized_name}”不存在") from exc
                raise ValueError(f"游戏“{normalized_name}”的简介已存在") from exc

            self._connection.commit()
            return int(cursor.lastrowid)

    def get_game_intro_by_game_name(self, game_name: str) -> dict[str, object] | None:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    id,
                    game_name,
                    game_intro,
                    created_at,
                    updated_at
                FROM game_intros
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
            "game_intro": row["game_intro"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_all_game_intros_with_game_name(self) -> list[dict[str, object]]:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    gi.id,
                    gi.game_name,
                    gi.game_intro,
                    gi.created_at,
                    gi.updated_at
                FROM game_intros AS gi
                ORDER BY gi.game_name COLLATE NOCASE ASC;
                """
            )
            rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "game_name": row["game_name"],
                "game_intro": row["game_intro"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def update_game_intro(self, game_name: str, new_game_intro: str) -> bool:
        normalized_name = game_name.strip()
        normalized_intro = new_game_intro.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")
        if not normalized_intro:
            raise ValueError("游戏简介不能为空")

        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE game_intros
                SET
                    game_intro = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE game_name = ?;
                """,
                (normalized_intro, normalized_name),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def delete_game_intro(self, game_name: str) -> int:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")

        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM game_intros WHERE game_name = ?;",
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

    def add_dialogue(
        self,
        game_id: int,
        character_name_original: str | None,
        dialog_text_original: str,
        dialog_text_translated: str | None = None,
    ) -> int:
        normalized_character_name_original = (
            character_name_original.strip()
            if isinstance(character_name_original, str)
            else ""
        )
        normalized_dialog_text_original = dialog_text_original.strip()
        normalized_dialog_text_translated = (
            dialog_text_translated.strip()
            if isinstance(dialog_text_translated, str)
            else ""
        )
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")
        self._validate_character_reference(game_id, normalized_character_name_original or None)

        with self._lock:
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO dialogues (
                        character_name_original,
                        dialog_text_original,
                        dialog_text_translated,
                        game_id
                    ) VALUES (?, ?, ?, ?);
                    """,
                    (
                        normalized_character_name_original or None,
                        normalized_dialog_text_original,
                        normalized_dialog_text_translated,
                        game_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"写入对话失败: {exc}") from exc
            self._connection.commit()
            return int(cursor.lastrowid)

    def get_all_dialogues_with_game_name(self) -> list[dict[str, object]]:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    d.id,
                    d.character_name_original,
                    c.name_translated,
                    d.dialog_text_original,
                    d.dialog_text_translated,
                    d.game_id,
                    g.game_name,
                    d.created_at
                FROM dialogues AS d
                LEFT JOIN games AS g ON d.game_id = g.id
                LEFT JOIN characters AS c
                    ON d.character_name_original = c.name_original
                    AND d.game_id = c.game_id
                ORDER BY d.created_at DESC, d.id DESC;
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "character_name_original": row["character_name_original"],
                "name_translated": row["name_translated"],
                "dialog_text_original": row["dialog_text_original"],
                "dialog_text_translated": row["dialog_text_translated"],
                "game_id": row["game_id"],
                "game_name": row["game_name"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_all_summaries_with_game_name(self) -> list[dict[str, object]]:
        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    s.id,
                    g.game_name,
                    s.content,
                    s.start_conversation_id,
                    s.end_conversation_id,
                    s.created_at
                FROM summaries AS s
                LEFT JOIN games AS g ON s.game_id = g.id
                ORDER BY s.id DESC;
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "game_name": row["game_name"],
                "content": row["content"],
                "start_conversation_id": row["start_conversation_id"],
                "end_conversation_id": row["end_conversation_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def update_dialogue(
        self,
        dialogue_id: int,
        character_name_original: str | None,
        dialog_text_original: str,
        dialog_text_translated: str | None = None,
    ) -> bool:
        normalized_character_name_original = (
            character_name_original.strip()
            if isinstance(character_name_original, str)
            else ""
        )
        normalized_dialog_text_original = dialog_text_original.strip()
        normalized_dialog_text_translated = (
            dialog_text_translated.strip()
            if isinstance(dialog_text_translated, str)
            else ""
        )
        if dialogue_id <= 0:
            raise ValueError("dialogue_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT game_id
                FROM dialogues
                WHERE id = ?;
                """,
                (dialogue_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            game_id = int(row["game_id"])

        self._validate_character_reference(game_id, normalized_character_name_original or None)

        with self._lock:
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE dialogues
                    SET
                        character_name_original = ?,
                        dialog_text_original = ?,
                        dialog_text_translated = ?
                    WHERE id = ?;
                    """,
                    (
                        normalized_character_name_original or None,
                        normalized_dialog_text_original,
                        normalized_dialog_text_translated,
                        dialogue_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"更新对话失败: {exc}") from exc
            self._connection.commit()
            return cursor.rowcount > 0

    def delete_dialogue(self, dialogue_id: int) -> bool:
        if dialogue_id <= 0:
            raise ValueError("dialogue_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM dialogues WHERE id = ?;",
                (dialogue_id,),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def update_summary(self, summary_id: int, new_content: str) -> bool:
        normalized_content = new_content.strip()
        if summary_id <= 0:
            raise ValueError("summary_id 必须为正整数")
        if not normalized_content:
            raise ValueError("摘要内容不能为空")

        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE summaries
                SET content = ?
                WHERE id = ?;
                """,
                (normalized_content, summary_id),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def delete_summary(self, summary_id: int) -> bool:
        if summary_id <= 0:
            raise ValueError("summary_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM summaries WHERE id = ?;",
                (summary_id,),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def clear_dialogues(self) -> int:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM dialogues;")
            self._connection.commit()
            return int(cursor.rowcount)

    def add_summary(
        self,
        game_id: int,
        content: str,
        start_id: int | None,
        end_id: int | None,
    ) -> int:
        normalized_content = content.strip()
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")
        if not normalized_content:
            raise ValueError("摘要内容不能为空")
        if start_id is not None and start_id <= 0:
            raise ValueError("start_id 必须为正整数或 None")
        if end_id is not None and end_id <= 0:
            raise ValueError("end_id 必须为正整数或 None")

        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO summaries (
                    game_id,
                    content,
                    start_conversation_id,
                    end_conversation_id
                ) VALUES (?, ?, ?, ?);
                """,
                (game_id, normalized_content, start_id, end_id),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def get_latest_summary(self, game_id: int) -> str:
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT content
                FROM summaries
                WHERE game_id = ?
                ORDER BY id DESC
                LIMIT 1;
                """,
                (game_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return ""
        return str(row["content"] or "")

    def get_latest_summary_record(self, game_id: int) -> dict[str, object] | None:
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    id,
                    game_id,
                    content,
                    start_conversation_id,
                    end_conversation_id,
                    created_at
                FROM summaries
                WHERE game_id = ?
                ORDER BY id DESC
                LIMIT 1;
                """,
                (game_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None

        return {
            "id": row["id"],
            "game_id": row["game_id"],
            "content": row["content"],
            "start_conversation_id": row["start_conversation_id"],
            "end_conversation_id": row["end_conversation_id"],
            "created_at": row["created_at"],
        }

    def get_dialogues_by_game_range(
        self,
        game_id: int,
        start_id: int,
        end_id: int,
    ) -> list[dict[str, object]]:
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")
        if start_id <= 0:
            raise ValueError("start_id 必须为正整数")
        if end_id <= 0:
            raise ValueError("end_id 必须为正整数")
        if end_id < start_id:
            return []

        with self._lock:
            cursor = self._connection.execute(
                """
                SELECT
                    id,
                    game_id,
                    character_name_original,
                    dialog_text_original,
                    dialog_text_translated,
                    created_at
                FROM dialogues
                WHERE game_id = ?
                    AND id >= ?
                    AND id <= ?
                ORDER BY id ASC;
                """,
                (game_id, start_id, end_id),
            )
            rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "game_id": row["game_id"],
                "character_name_original": row["character_name_original"],
                "dialog_text_original": row["dialog_text_original"],
                "dialog_text_translated": row["dialog_text_translated"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _validate_character_reference(
        self,
        game_id: int,
        character_name_original: str | None,
    ) -> None:
        if game_id <= 0:
            raise ValueError("game_id 必须为正整数")
        if character_name_original is None:
            return
        if not character_name_original.strip():
            return

        normalized_name_original = character_name_original.strip()
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
            if cursor.fetchone() is None:
                raise ValueError(
                    f"人物“{normalized_name_original}”不在该游戏下，无法关联对话"
                )

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

    def get_character_by_name_original(
        self,
        name_original: str,
        game_id: int,
    ) -> dict[str, object] | None:
        normalized_name_original = name_original.strip()
        if not normalized_name_original:
            raise ValueError("原文名称不能为空")
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
                WHERE name_original = ? AND game_id = ?
                LIMIT 1;
                """,
                (normalized_name_original, game_id),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "name_original": row["name_original"],
            "name_translated": row["name_translated"],
            "game_id": row["game_id"],
            "gender": row["gender"],
            "extra_info": row["extra_info"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

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
