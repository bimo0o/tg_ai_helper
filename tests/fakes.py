"""Small PostgreSQL protocol fake; production repository SQL is still exercised."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta


class MemoryPool:
    def __init__(self):
        self.settings = {}
        self.messages = []
        self.calls = []
        self.admin_id = None
        self.model = None
        self.events = {}
        self.profiles = {}

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query, *args):
        self.calls.append((query, args))
        if query.startswith("INSERT INTO music_profiles"):
            profile = self.profiles.get(args[0])
            if profile is None or profile["last_used_at"] < datetime.now(UTC) - timedelta(
                days=args[3]
            ):
                profile = {"data": {}, "last_used_at": datetime.now(UTC)}
                self.profiles[args[0]] = profile
            profile["data"][args[1]] = args[2]
            profile["last_used_at"] = datetime.now(UTC)
            return
        if query.startswith("DELETE FROM music_profiles WHERE user_id"):
            self.profiles.pop(args[0], None)
            return
        if query.startswith("INSERT INTO administrators"):
            if self.admin_id is None:
                self.admin_id = args[0]
            return "INSERT 0 1"
        if query.startswith("INSERT INTO application_settings"):
            if self.admin_id != args[0]:
                return "INSERT 0 0"
            self.model = args[1]
            return "INSERT 0 1"
        if query.startswith("INSERT INTO request_events"):
            self.events[args[0]] = {
                "user_id": args[2],
                "model": args[3],
                "status": "started",
                "mode": "unknown",
                "created_at": datetime.now(UTC),
                "cost_usd": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "duration_ms": None,
                "reserved_usd": args[4],
            }
            return
        if query.startswith("UPDATE request_events"):
            self.events[args[0]].update(
                zip(
                    [
                        "mode",
                        "status",
                        "duration_ms",
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "cost_usd",
                        "error_kind",
                    ],
                    args[1:],
                    strict=True,
                )
            )
            if args[7] is not None:
                self.events[args[0]]["reserved_usd"] = args[7]
            return
        owner = args[:2]
        if query.startswith("SELECT pg_advisory"):
            return
        if query.startswith("INSERT INTO user_settings"):
            self.settings.setdefault(owner, {"mode": "study", "temperature": 0.3})
        elif query.startswith("INSERT INTO messages"):
            self.messages.append(
                {
                    "owner": owner,
                    "role": args[2],
                    "content": args[3],
                    "mode": args[4],
                    "created_at": datetime.now(UTC),
                }
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
        if "AS global_spend" in query:
            events = list(self.events.values())
            return {
                "global_spend": sum(
                    row["reserved_usd"] for row in events if row["created_at"] >= args[1]
                ),
                "user_spend": sum(
                    row["reserved_usd"]
                    for row in events
                    if row["user_id"] == args[0] and row["created_at"] >= args[1]
                ),
                "day_count": sum(
                    row["user_id"] == args[0] and row["created_at"] >= args[1] for row in events
                ),
                "minute_count": sum(
                    row["user_id"] == args[0] and row["created_at"] >= args[2] for row in events
                ),
            }
        if "FROM request_events" in query:
            events = [row for row in self.events.values() if row["created_at"] >= args[0]]
            priced = [row for row in events if row["cost_usd"] is not None]
            durations = [row["duration_ms"] for row in events if row["duration_ms"] is not None]
            return {
                "requests": len(events),
                "users": len({row["user_id"] for row in events}),
                "successes": sum(row["status"] == "success" for row in events),
                "errors": sum(row["status"] == "error" for row in events),
                "pending": sum(row["status"] == "started" for row in events),
                "priced": len(priced),
                "reserved_usd": sum(row["reserved_usd"] for row in events),
                "cost_usd": sum(row["cost_usd"] for row in priced),
                "prompt_tokens": sum(row["prompt_tokens"] or 0 for row in events),
                "completion_tokens": sum(row["completion_tokens"] or 0 for row in events),
                "average_ms": sum(durations) / len(durations) if durations else None,
            }
        assert "WHERE chat_id=$1 AND user_id=$2" in query
        return dict(self.settings[args[:2]])

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if "FROM request_events" in query:
            users = {}
            for event in self.events.values():
                if event["created_at"] < args[0]:
                    continue
                row = users.setdefault(
                    event["user_id"],
                    {
                        "user_id": event["user_id"],
                        "requests": 0,
                        "errors": 0,
                        "priced": 0,
                        "cost_usd": 0,
                    },
                )
                row["requests"] += 1
                row["errors"] += event["status"] == "error"
                row["priced"] += event["cost_usd"] is not None
                row["cost_usd"] += event["cost_usd"] or 0
            return sorted(users.values(), key=lambda row: (-row["requests"], row["user_id"]))[
                args[1] : args[1] + 11
            ]
        assert "WHERE chat_id=$1 AND user_id=$2" in query
        assert "ORDER BY id DESC LIMIT $4" in query
        return [
            dict(row)
            for row in reversed(self.messages)
            if row["owner"] == args[:2]
            and row["mode"] == args[2]
            and row["created_at"] >= datetime.now(UTC) - timedelta(days=args[4])
        ][: args[3]]

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        if query.startswith("UPDATE music_profiles"):
            profile = self.profiles.get(args[0])
            if not profile or profile["last_used_at"] < datetime.now(UTC) - timedelta(days=args[1]):
                return None
            profile["last_used_at"] = datetime.now(UTC)
            return json.dumps(profile["data"])
        if "FROM administrators" in query:
            return self.admin_id == args[0]
        if "FROM application_settings" in query:
            return self.model
        raise AssertionError(query)
