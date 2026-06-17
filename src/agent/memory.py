from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from src.core.logging_utils import setup_logging
from src.core.settings import Settings

logger = setup_logging("agent.memory")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(text: str, limit: int) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


class ConversationMemoryStore:
    def __init__(self, settings: Settings, db):
        self.settings = settings
        self.db = db
        self.sessions = db[settings.conversation_sessions_collection]
        self.turns = db[settings.conversation_turns_collection]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        ttl_seconds = max(1, self.settings.conversation_ttl_days) * 24 * 60 * 60
        self.sessions.create_index([("conversation_id", ASCENDING)], unique=True, name="uq_conversation_id")
        self.sessions.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_session_user_updated")
        self.sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=ttl_seconds, name="ttl_session_expires")

        self.turns.create_index([("conversation_id", ASCENDING), ("seq", ASCENDING)], unique=True, name="uq_turn_sequence")
        self.turns.create_index([("conversation_id", ASCENDING), ("ts", DESCENDING)], name="idx_turn_conversation_ts")
        self.turns.create_index([("expires_at", ASCENDING)], expireAfterSeconds=ttl_seconds, name="ttl_turn_expires")

    def _expires_at(self) -> datetime:
        return _utc_now() + timedelta(days=max(1, self.settings.conversation_ttl_days))

    def open_conversation(self, conversation_id: str | None, user_id: str | None = None) -> str:
        cid = conversation_id or f"conv_{uuid4().hex}"
        now = _utc_now()
        update = {
            "$setOnInsert": {
                "conversation_id": cid,
                "created_at": now,
                "next_seq": 0,
                "summary": "",
            },
            "$set": {
                "updated_at": now,
                "expires_at": self._expires_at(),
            },
        }
        if user_id:
            update["$set"]["user_id"] = user_id
        self.sessions.update_one({"conversation_id": cid}, update, upsert=True)
        return cid

    def append_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
        tool_name: str | None = None,
        user_id: str | None = None,
    ) -> int:
        now = _utc_now()
        session_update = {
            "$inc": {"next_seq": 1},
            "$setOnInsert": {
                "conversation_id": conversation_id,
                "created_at": now,
                "summary": "",
            },
            "$set": {
                "updated_at": now,
                "expires_at": self._expires_at(),
            },
        }
        if user_id:
            session_update["$set"]["user_id"] = user_id
        session = self.sessions.find_one_and_update(
            {"conversation_id": conversation_id},
            session_update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        seq = int(session.get("next_seq", 0))
        doc = {
            "conversation_id": conversation_id,
            "seq": seq,
            "role": role,
            "content": content,
            "tool_name": tool_name,
            "metadata": metadata or {},
            "ts": now,
            "expires_at": self._expires_at(),
        }
        self.turns.update_one({"conversation_id": conversation_id, "seq": seq}, {"$setOnInsert": doc}, upsert=True)
        return seq

    def get_context(self, conversation_id: str, max_turns: int) -> dict:
        session = self.sessions.find_one({"conversation_id": conversation_id}, {"_id": 0, "summary": 1, "state": 1})
        turns = list(
            self.turns.find(
                {"conversation_id": conversation_id, "role": {"$in": ["user", "assistant"]}},
                {"_id": 0, "seq": 1, "role": 1, "content": 1, "ts": 1},
            )
            .sort([("seq", DESCENDING)])
            .limit(max(1, max_turns))
        )
        turns.reverse()
        return {
            "conversation_id": conversation_id,
            "summary": (session or {}).get("summary") or "",
            "state": (session or {}).get("state") or {},
            "turns": turns,
        }

    def update_state(self, conversation_id: str, patch: dict) -> None:
        if not patch:
            return
        now = _utc_now()
        existing = self.sessions.find_one({"conversation_id": conversation_id}, {"_id": 0, "state": 1}) or {}
        state = existing.get("state")
        merged_state = dict(state) if isinstance(state, dict) else {}
        merged_state.update(patch)
        self.sessions.update_one(
            {"conversation_id": conversation_id},
            {
                "$setOnInsert": {
                    "conversation_id": conversation_id,
                    "created_at": now,
                    "next_seq": 0,
                    "summary": "",
                },
                "$set": {
                    "updated_at": now,
                    "expires_at": self._expires_at(),
                    "state": merged_state,
                },
            },
            upsert=True,
        )

    def get_history(self, conversation_id: str, limit: int = 100) -> list[dict]:
        rows = list(
            self.turns.find(
                {"conversation_id": conversation_id},
                {"_id": 0, "seq": 1, "role": 1, "content": 1, "tool_name": 1, "metadata": 1, "ts": 1},
            )
            .sort([("seq", DESCENDING)])
            .limit(max(1, limit))
        )
        rows.reverse()
        for row in rows:
            ts = row.get("ts")
            if isinstance(ts, datetime):
                row["ts"] = ts.astimezone(timezone.utc).isoformat()
        return rows

    def get_session(self, conversation_id: str) -> dict | None:
        row = self.sessions.find_one({"conversation_id": conversation_id}, {"_id": 0})
        if not row:
            return None
        for key in ("created_at", "updated_at", "expires_at", "summary_updated_at"):
            value = row.get(key)
            if isinstance(value, datetime):
                row[key] = value.astimezone(timezone.utc).isoformat()
        return row

    def delete_conversation(self, conversation_id: str) -> dict:
        turns_deleted = self.turns.delete_many({"conversation_id": conversation_id}).deleted_count
        session_deleted = self.sessions.delete_one({"conversation_id": conversation_id}).deleted_count
        return {
            "conversation_id": conversation_id,
            "turns_deleted": int(turns_deleted),
            "session_deleted": int(session_deleted),
        }

    def refresh_summary(self, conversation_id: str) -> None:
        max_context = max(1, self.settings.assistant_max_context_turns)
        trigger = max(max_context + 1, self.settings.assistant_summary_trigger_turns)
        total = self.turns.count_documents({"conversation_id": conversation_id, "role": {"$in": ["user", "assistant"]}})
        if total < trigger:
            return

        older_target = max(0, total - max_context)
        if older_target <= 0:
            return

        older = list(
            self.turns.find(
                {"conversation_id": conversation_id, "role": {"$in": ["user", "assistant"]}},
                {"_id": 0, "role": 1, "content": 1},
            )
            .sort([("seq", ASCENDING)])
            .limit(min(older_target, 40))
        )
        if not older:
            return

        lines: list[str] = []
        for row in older:
            role = "User" if row.get("role") == "user" else "Assistant"
            text = _truncate(str(row.get("content", "")), 220)
            if text:
                lines.append(f"{role}: {text}")
        if not lines:
            return
        summary = "Conversation memory summary:\n" + "\n".join(lines[-20:])
        self.sessions.update_one(
            {"conversation_id": conversation_id},
            {
                "$set": {
                    "summary": summary,
                    "summary_updated_at": _utc_now(),
                    "updated_at": _utc_now(),
                    "expires_at": self._expires_at(),
                }
            },
            upsert=True,
        )
        logger.debug("Conversation summary refreshed: conversation_id=%s lines=%s", conversation_id, len(lines))
