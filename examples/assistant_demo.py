"""Run a real signed-in Claude Code client against LearnHub HTTP.

No built-in filesystem/shell tools or other MCP servers are enabled. Captures
actual model tool choices and answers, not a fabricated conversation. Start the
mock LMS and HTTP server first. Uses the existing Claude account/subscription.
"""
import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

PROMPT = ("Using LearnHub only, find the first published course in category Security. "
          "For that course, which learners have not started? Show at most five names and "
          "state the total matching learner count. Then verify that total using a follow-up "
          "request fetching at most one participant, and explain how you got the total. "
          "Do not list the whole user directory. Make real tool calls; do not guess. "
          "This LMS contains fictional demo data.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8001/mcp')
    parser.add_argument('--output', type=Path, default=Path('build/assistant-demo'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = {'mcpServers': {'learnhub': {'type': 'http', 'url': args.url,
                                       'headers': {'Authorization': 'Bearer demo-token-acme'}}}}
    events = []
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='learnhub-assistant-') as temporary:
        path = Path(temporary) / 'mcp.json'
        path.write_text(json.dumps(config))
        command = ['claude', '-p', '--verbose', '--output-format', 'stream-json',
                   '--no-session-persistence', '--strict-mcp-config', '--mcp-config', str(path),
                   '--tools', '', '--allowedTools', 'mcp__learnhub__*', '--disable-slash-commands',
                   '--setting-sources', '', '--max-budget-usd', '1',
                   '--system-prompt', 'You are a training manager evaluating a fictional LMS. Use only the provided LearnHub tools. Report accurate counts and distinguish shown rows from total matches.',
                   PROMPT]
        with (args.output / 'client-stderr.log').open('w') as errors:
            process = subprocess.Popen(command, cwd=temporary, stdout=subprocess.PIPE,
                                       stderr=errors, text=True, bufsize=1)
            print('USER: ' + PROMPT, flush=True)
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get('type')
                if kind in ('assistant', 'user'):
                    message = event.get('message', {})
                    for block in message.get('content', []):
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'tool_use':
                            payload = {'type': 'tool_call', 'name': block['name'], 'arguments': block['input']}
                        elif block.get('type') == 'tool_result':
                            payload = {'type': 'tool_result', 'content': block.get('content'), 'is_error': block.get('is_error', False)}
                        elif block.get('type') == 'text' and kind == 'assistant':
                            payload = {'type': 'answer', 'text': block['text']}
                        else:
                            continue
                        payload['elapsed_seconds'] = round(time.monotonic() - start, 3)
                        events.append(payload)
                        print(json.dumps(payload, ensure_ascii=False), flush=True)
                elif kind == 'result':
                    summary = {'type': 'completion', 'is_error': event.get('is_error'),
                               'result': event.get('result'), 'elapsed_seconds': round(time.monotonic() - start, 3)}
                    events.append(summary)
            code = process.wait()
    document = {'label': 'Actual assistant execution with fictional LMS data', 'prompt': PROMPT, 'events': events}
    (args.output / 'transcript.json').write_text(json.dumps(document, indent=2, ensure_ascii=False))
    calls = [e for e in events if e['type'] == 'tool_call']
    if code or not calls or not events or events[-1].get('is_error'):
        raise SystemExit('Assistant run did not complete successfully; inspect local capture logs.')
    print(f"Captured {len(calls)} real tool calls in {args.output / 'transcript.json'}")


if __name__ == '__main__':
    main()
