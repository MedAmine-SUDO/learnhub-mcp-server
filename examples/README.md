# Demonstrations

All examples use fictional Acme/Globex data and public demo tokens. Keep the mock LMS running.

- `demo_session.py`: scripted protocol discovery, inline/stored results, paging, details and cross-tenant rejection over real stdio MCP.
- `training_manager_demo.py`: scripted course → filtered participants → total-count follow-up, through a real MCP client. This verifies the business workflow, but an LLM does not choose the calls.
- `assistant_demo.py`: a real signed-in Claude Code client chooses LearnHub HTTP tools and answers the training-manager question. Built-in filesystem/shell tools are disabled, and only this MCP server is configured. It uses your existing Claude authentication/subscription, with a $1 API budget flag; no provider key is stored here.

To capture a real assistant run:

```bash
# Terminal 1
uv run learnhub-mock-api
# Terminal 2
uv run learnhub-mcp-http
# Terminal 3, requires an installed, authenticated Claude CLI
uv run python examples/assistant_demo.py
```

The runner writes an actual tool-call/result/answer transcript to `build/assistant-demo/transcript.json`, without the CLI's account/session metadata. Inspect it before sharing. Never point this recorder at real customer data unless disclosure is authorized. A captured run proves this client/path at that time, not universal compatibility or model accuracy.
