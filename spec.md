# Lab 2 and reminder delivery specification

## Scope

Extend the existing bot with a stdio MCP server, agent routing, weather, a synthetic
schedule, confirmed reminders, and actual Telegram delivery. Preserve existing modes.
Reminder editing and recurrence are deferred. The user confirmed the original
evaluation/lab2-routing.json will not be supplied. Keep official benchmark results
unverified; an explicitly labeled authored supplementary dataset may be used for a separate experiment.

## Observable scenarios

- `/tools` lists tools discovered through MCP and their read/write classification.
- `/timezone Europe/Moscow` saves an IANA zone and displays local time; no argument displays the current setting.
- A local-time reminder without a saved zone requests zone configuration, not guessed geography.
- A reminder proposal shows exact text, date, time and zone; it creates no reminder before confirmation.
- Confirmation expires after five minutes and is bound to the owner, immutable arguments and action.
- Duplicate update or confirmation produces one action and one reminder in PostgreSQL.
- Cancelled, expired and foreign confirmations make no change.
- Confirmed reminders survive restart and are delivered by a database-backed worker without LLM calls.
- Cancellation succeeds before dispatch; an in-flight send is reported honestly.
- Delays up to 24 hours are delivered with the original due time and a delay notice; older jobs become missed.
- Ambiguous delivery results become unconfirmed, without blind automatic resending.
- `/why` displays the last safe audit entry, never chain of thought, prompts, secrets or other owners' data.
- MCP failure leaves ordinary conversation available; unavailable operations report failure, not invented success.

## MCP contracts

All input models reject extra fields; input/output schemas are discovered through MCP.
Tool results use structured content. Tool annotations never replace application write policy.

- `get_weather(city: string[1..100])`: normalized city/country, temperature C, weather code,
  wind speed/unit, observation time/timezone; alternatively distinct candidate cities or not-found.
  Never silently select among ambiguous names. Only fixed weather/geocoding endpoints are called.
- `get_schedule(date: YYYY-MM-DD)`: lessons with start/end/title/location; empty lessons is valid.
  Source is configured by the operator, never a model-supplied path.
- `add_reminder(text: string[1..500], remind_at: ISO datetime with explicit offset)`:
  future time; returns reminder ID, normalized time/zone and created flag.
  Owner/action identity belongs to signed protocol metadata, not tool arguments.
  Server verifies signature, expiry, confirmed database action, matching arguments and owner.
- `schedule://current-week`: read-only Monday–Sunday dates and lessons in the configured zone.

## State and trust

Prepared action: pending -> confirmed -> executed; pending may become cancelled or expired.
Reminder: scheduled -> sending -> sent / unconfirmed / blocked / missed;
scheduled may become cancelled. A sent message does not imply the user read it.
Unique constraints enforce update/action identity. Worker claims and completion tokens prevent stale writes.
An expired worker lease does not justify replaying a potentially accepted Telegram send.
IANA conversion rejects nonexistent or ambiguous local times. Time is injectable in tests.
Default timezone setup asks for the user's current local HH:MM, infers a fixed UTC offset
in 15-minute increments and reports the resulting local clock. Ambiguous day boundaries
require the local date as well. Fixed offsets do not imply a geographical/DST region;
users must update them after clock changes or travel. Legacy IANA input stays supported.
Relative reminders use a host-side validated delay and server clock, requiring no timezone.
Fresh trusted timezone state follows stale dialogue context before the current request.
Profiles, model arguments, schedule text and external results never authorize a write.

## Agent limits

Use native tool calling, never regex routing. At most three LLM rounds and two MCP calls per
message, with a 90-second overall deadline. Request one reminder per message initially.
Translation and temperature experiments do not invoke tools. Explicit web search remains
separate; agent tools initially apply to chat/study/music. Confirmation is a deterministic button action.

## Retention and acceptance

Retain finished reminders 30 days and audit 90 days. Do not retain full reminder text in audit.
Active reminders are never purged as historical records. Preserve user settings.
Required checks include rejection/expiry/ownership, concurrent creation and dispatch on PostgreSQL,
week/year boundaries, DST, MCP disconnection, schema violations and injected instructions.
Routing experiment thresholds: 10/12 decisions, 8/9 arguments on the unmodified original dataset.
Independent MCP Inspector and actual Telegram delivery require manual verification.
