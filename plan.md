# Implementation plan

- [x] Read updated lab 2 requirements and agree scope with the user.
- [x] Record project rules, contracts, observable outcomes and delivery tradeoffs.
- [x] Pin official MCP SDK and compatible dependencies; validate API locally and document choices.
- [x] Implement IANA/date validation and PostgreSQL action/reminder/audit schema.
- [x] Verify duplicate updates/confirmations, owner isolation and expiry on real PostgreSQL.
- [ ] Implement stdio MCP server/client, discovery, schemas, signed write context and output checks.
- [ ] Add confirmed reminder creation, list, cancellation and timezone UI.
- [ ] Add durable delivery worker and tests for late, blocked, unknown and interrupted delivery.
- [ ] Add weather, schedule and weekly resource with fixed endpoints/configured synthetic data.
- [ ] Add bounded native tool-calling loop, `/tools`, `/why`, safe audit and degraded MCP behavior.
- [ ] Prepare evaluation runner; obtain original lab2-routing.json. Do not execute paid runs as agent.
- [ ] Complete regression/CI, documentation, independent MCP inspection instructions and deployment.
- [ ] User verifies live delivery and routing experiment; creates submission tag after acceptance.

Editing and recurrence are deferred. Delivery is an authorized product extension beyond lab 2.
For each stage report the scenario, observed result and limits of verification.
