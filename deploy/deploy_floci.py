"""Deploy and verify the built Lambda on a local Floci instance only.

See docs/DEPLOYMENT.md. No real AWS resources are created by this script.
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import boto3

ROOT = Path(__file__).resolve().parent.parent
FUNCTION = 'learnhub-mcp'
REGISTRY_BUCKET = 'learnhub-registry'
RESULTS_BUCKET = 'learnhub-results'
ENDPOINT = os.getenv('FLOCI_ENDPOINT', 'http://127.0.0.1:14566')
INTERNAL_ENDPOINT = os.environ.get('FLOCI_CONTAINER_ENDPOINT', 'http://172.17.0.3:4566')
MOCK_URL = os.getenv('FLOCI_MOCK_API_URL', 'http://172.17.0.1:18090')


def client(service):
    if urlparse(ENDPOINT).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('This script only targets a loopback Floci endpoint')
    return boto3.client(service, endpoint_url=ENDPOINT, region_name='us-east-1',
                        aws_access_key_id='test', aws_secret_access_key='test')


def rpc(lam, method, params=None, token='demo-token-acme'):
    headers = {'host': 'testserver', 'content-type': 'application/json',
               'accept': 'application/json, text/event-stream', 'mcp-protocol-version': '2025-06-18'}
    if token:
        headers['authorization'] = 'Bearer ' + token
    event = {'version': '2.0', 'rawPath': '/mcp', 'rawQueryString': '', 'isBase64Encoded': False,
             'requestContext': {'http': {'method': 'POST', 'path': '/mcp', 'sourceIp': '127.0.0.1'}},
             'headers': headers, 'body': json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})}
    raw = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(event))
    body = json.loads(raw['Payload'].read())
    if 'FunctionError' in raw:
        raise RuntimeError(f"Lambda execution failed: {body.get('errorType', 'unknown')}")
    return body


def tool(lam, name, arguments=None, token='demo-token-acme'):
    response = rpc(lam, 'tools/call', {'name': name, 'arguments': arguments or {}}, token)
    assert response['statusCode'] == 200, response
    result = json.loads(response['body'])['result']
    return json.loads(result['content'][0]['text']), result.get('isError', False)


def main():
    lam, s3 = client('lambda'), client('s3')
    for bucket in (REGISTRY_BUCKET, RESULTS_BUCKET):
        if bucket not in [b['Name'] for b in s3.list_buckets()['Buckets']]:
            s3.create_bucket(Bucket=bucket)
    s3.put_bucket_lifecycle_configuration(Bucket=RESULTS_BUCKET, LifecycleConfiguration={
        'Rules': [{'ID': 'expire-results', 'Status': 'Enabled', 'Filter': {'Prefix': 'results/'},
                   'Expiration': {'Days': 1}}]})
    s3.upload_file(str(ROOT / 'tenants.example.json'), REGISTRY_BUCKET, 'tenants.json')
    env = {'Variables': {
        'LEARNHUB_API_BASE_URL': MOCK_URL,
        'LEARNHUB_TENANT_REGISTRY': f's3://{REGISTRY_BUCKET}/tenants.json',
        'LEARNHUB_RESULT_STORE_URI': f's3://{RESULTS_BUCKET}/results',
        'LEARNHUB_ALLOWED_HOSTS': 'testserver',
        'AWS_ENDPOINT_URL': INTERNAL_ENDPOINT, 'AWS_DEFAULT_REGION': 'us-east-1',
    }}
    code = (ROOT / 'build/lambda.zip').read_bytes()
    try:
        lam.get_function(FunctionName=FUNCTION)
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=env)
    except lam.exceptions.ResourceNotFoundException:
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12',
            Role='arn:aws:iam::000000000000:role/lambda-role',
            Handler='learnhub_mcp.lambda_handler.lambda_handler', Timeout=30,
            MemorySize=512, Code={'ZipFile': code}, Environment=env)
    for _ in range(30):
        cfg = lam.get_function(FunctionName=FUNCTION)['Configuration']
        if cfg.get('State') in ('Active', None) and cfg.get('LastUpdateStatus') in ('Successful', None):
            break
        time.sleep(1)
    else:
        raise RuntimeError('Function did not become ready')
    init = rpc(lam, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                 'clientInfo': {'name': 'floci-verification', 'version': '1'}})
    assert init['statusCode'] == 200
    print('PASS packaged Lambda initializes through the SDK HTTP adapter')
    stored, error = tool(lam, 'list_users', {'max_results': 250})
    assert not error and stored['count'] == 250
    rows = []
    for offset in (0, 200):
        page, error = tool(lam, 'read_stored_result', {'result_id': stored['result_id'], 'offset': offset, 'limit': 200})
        assert not error
        rows.extend(page['items'])
    assert len(rows) == len({u['id'] for u in rows}) == 250
    print('PASS 250 unique users; snapshot survives separate Lambda lifespans via S3')
    listed, _ = tool(lam, 'list_stored_results', token='demo-token-globex')
    assert listed['results'] == []
    denied, error = tool(lam, 'read_stored_result', {'result_id': stored['result_id']}, 'demo-token-globex')
    assert error and denied['error'] == 'NotFoundError'
    assert rpc(lam, 'tools/list', token='unknown')['statusCode'] == 401
    print('PASS foreign snapshot and invalid client token rejected')


if __name__ == '__main__':
    main()
