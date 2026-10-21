"""Small PostgreSQL protocol fake; production repository SQL is still exercised."""

from contextlib import asynccontextmanager


class MemoryPool:
    def __init__(self):
        self.settings = {}
        self.messages = []
        self.calls = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query, *args):
        self.calls.append((query, args))
        owner = args[:2]
        if query.startswith("SELECT pg_advisory"):
            return
        if query.startswith("INSERT INTO user_settings"):
            self.settings.setdefault(owner, {"mode": "study", "temperature": 0.3})
        elif query.startswith("INSERT INTO messages"):
            self.messages.append(
                {"owner": owner, "role": args[2], "content": args[3], "mode": args[4]}
            )
        elif query.startswith("DELETE FROM messages"):
            self.messages = [row for row in self.messages if row["owner"] != owner]
        elif query.startswith("UPDATE user_settings SET mode"):
            self.settings[owner]["mode"] = args[2]
        elif query.startswith("UPDATE user_settings SET temperature"):
            self.settings[owner]["temperature"] = args[2]
        else:
            raise AssertionError(query)

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        assert "WHERE chat_id=$1 AND user_id=$2" in query
        return dict(self.settings[args[:2]])

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        assert "WHERE chat_id=$1 AND user_id=$2" in query
        assert "ORDER BY id DESC LIMIT $4" in query
        return [
            dict(row)
            for row in reversed(self.messages)
            if row["owner"] == args[:2] and row["mode"] == args[2]
        ][: args[3]]
