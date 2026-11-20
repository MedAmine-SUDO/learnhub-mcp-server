"""Exercise real boto3 request serialization and ownership across store instances."""
import io
import json

import boto3
import pytest
from botocore.stub import ANY, Stubber

from learnhub_mcp.errors import NotFoundError
from learnhub_mcp.result_store import S3ResultStore


def test_s3_snapshot_cross_worker_owner_and_expiry():
    s3 = boto3.client('s3', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
    now = [100.0]
    writer = S3ResultStore('s3://test-bucket/results', client=s3, clock=lambda: now[0])
    reader = S3ResultStore('s3://test-bucket/results', client=s3, clock=lambda: now[0])
    with Stubber(s3) as stub:
        stub.add_response('put_object', {}, {'Bucket': 'test-bucket', 'Key': ANY, 'Body': ANY,
                                             'ContentType': 'application/json', 'ServerSideEncryption': 'AES256'})
        meta = writer.save('list_users', {}, [{'id': 10001}], 1, tenant_id=1)
        key = 'results/tenant-1/' + meta['result_id'] + '.json'
        body = json.dumps({'meta': meta, 'items': [{'id': 10001}]}).encode()
        stub.add_response('get_object', {'Body': io.BytesIO(body)}, {'Bucket': 'test-bucket', 'Key': key})
        assert reader.read(meta['result_id'], tenant_id=1)['items'] == [{'id': 10001}]
        stub.add_response('list_objects_v2', {}, {'Bucket': 'test-bucket', 'Prefix': 'results/tenant-2/'})
        assert reader.list(tenant_id=2) == []
        stub.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404,
                              expected_params={'Bucket': 'test-bucket', 'Key': key.replace('tenant-1/', 'tenant-2/')})
        with pytest.raises(NotFoundError):
            reader.read(meta['result_id'], tenant_id=2)
        now[0] += 86401
        stub.add_response('get_object', {'Body': io.BytesIO(body)}, {'Bucket': 'test-bucket', 'Key': key})
        stub.add_response('delete_object', {}, {'Bucket': 'test-bucket', 'Key': key})
        with pytest.raises(NotFoundError):
            reader.read(meta['result_id'], tenant_id=1)
        stub.assert_no_pending_responses()
