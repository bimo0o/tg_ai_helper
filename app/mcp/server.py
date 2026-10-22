"""Protocol server with explicit schemas and a separate authority check for writes."""

import json
import logging

import mcp.types as types
from mcp.server.lowlevel import Server

from app.mcp.authorization import WriteAuthority
from app.mcp.contracts import DESCRIPTIONS, INPUTS, OUTPUTS, ReminderOutput

logger = logging.getLogger(__name__)
WEEK_URI = "schedule://current-week"


def create_server(reminders, weather, schedule, authority: WriteAuthority) -> Server:
    tools = [
        types.Tool(
            name=name,
            description=DESCRIPTIONS[name],
            inputSchema=model.model_json_schema(),
            outputSchema=OUTPUTS[name].model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=name != "add_reminder",
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=name == "get_weather",
            ),
        )
        for name, model in INPUTS.items()
    ]

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        try:
            if params.name not in INPUTS:
                raise ValueError
            arguments = INPUTS[params.name].model_validate(params.arguments or {}).model_dump()
            if params.name == "get_weather":
                output = await weather.current(arguments["city"])
            elif params.name == "get_schedule":
                output = schedule.day(arguments["date"])
            else:
                grant = authority.verify(ctx.meta, arguments)
                row, created = await reminders.execute(
                    (grant.chat_id, grant.user_id), grant.action_id, **arguments
                )
                output = ReminderOutput(
                    id=row["id"],
                    remind_at=row["remind_at"],
                    timezone=row["timezone"],
                    created=created,
                )
            value = OUTPUTS[params.name].model_validate(output).model_dump(mode="json")
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(value, ensure_ascii=False))],
                structuredContent=value,
            )
        except Exception as error:
            # SDK validation and DB errors can embed argument values. Never log their message.
            logger.warning("MCP tool failed kind=%s", type(error).__name__)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        text="Tool unavailable, invalid input or authorization denied"
                    )
                ],
                isError=True,
            )

    async def list_resources(ctx, params):
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=WEEK_URI,
                    name="Current week schedule",
                    description="Расписание с понедельника по воскресенье в настроенной зоне.",
                    mimeType="application/json",
                )
            ]
        )

    async def read_resource(ctx, params):
        if str(params.uri) != WEEK_URI:
            raise ValueError("Unknown resource")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=WEEK_URI,
                    mimeType="application/json",
                    text=schedule.week().model_dump_json(),
                )
            ]
        )

    return Server(
        "tg-assistant-tools",
        version="1.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
        get_tool_input_schema=lambda name: (
            INPUTS[name].model_json_schema() if name in INPUTS else None
        ),
    )
