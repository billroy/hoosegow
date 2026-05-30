## Bullpen Ticket State

- Bullpen tickets/tasks are live application state. Do not create, list, or
  update tickets by writing `.bullpen/tasks` files directly.
- Prefer Bullpen MCP tools (`mcp__bullpen__*`) when they are exposed in the
  current session.
- If Bullpen MCP tools are not exposed but shell commands are available, use the
  server-backed Bullpen ticket CLI against the running Bullpen server on
  `127.0.0.1:5050`, for example:

```bash
python3 /Users/bill/aistuff/bullpen/bullpen.py ticket --workspace /path/to/project --host 127.0.0.1 --port 5050 create \
  --title "Ticket title" \
  --status backlog \
  --description "Markdown body"
```

- For longer ticket text, write the text outside `.bullpen/tasks` and pass it
  with `--description-file` or `--body-file`.
- If neither MCP tools nor the server-backed ticket CLI can reach the running
  Bullpen server, stop and report that ticket writes are unavailable.
- Direct task-file writes bypass the running Bullpen Flask/Socket.IO server, so
  browser clients do not receive `task:created` or `task:updated` events and can
  show stale or corrupted task state.
