"""Deterministic business workflow through the real stdio MCP client."""
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main():
    params = StdioServerParameters(command='uv', args=['run', '--quiet', 'learnhub-mcp'], cwd=str(ROOT),
        env={**os.environ, 'LEARNHUB_CLIENT_TOKEN': 'demo-token-acme',
             'LEARNHUB_TENANT_REGISTRY': str(ROOT / 'tenants.example.json')})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        async def call(name, args):
            reply = await session.call_tool(name, args)
            if reply.is_error:
                raise RuntimeError(reply.content[0].text)
            return json.loads(reply.content[0].text)
        courses = await call('list_trainings', {'status': 'published', 'category': 'Security', 'max_results': 1})
        course = courses['items'][0]
        report = await call('list_training_participants', {
            'training_id': course['id'], 'completion': 'not_started', 'max_results': 5})
        print(f"Course: {course['title']} (id {course['id']})")
        print(f"Not started: {report['total_available']} learners; showing {report['count']}")
        for learner in report['items']:
            print(f"- {learner['name']}: {learner['completion']}")
        total = await call('list_training_participants', {
            'training_id': course['id'], 'completion': 'not_started', 'max_results': 1})
        assert total['total_available'] == report['total_available']
        print(f"Follow-up total: {total['total_available']} (only one record fetched)")
        print('All data is fictional. This runner is scripted, not an LLM conversation.')


if __name__ == '__main__':
    asyncio.run(main())
