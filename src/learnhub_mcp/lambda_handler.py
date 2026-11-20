"""Lambda Function URL adapter for the same SDK-backed ASGI application.

A fresh ASGI lifespan per invocation avoids leaked event-loop state. Results
must use shared S3 storage; no session or /tmp affinity is assumed.
"""
import re
from dataclasses import replace

from .config import Settings
from .http_server import create_app


def lambda_handler(event, context):
    from mangum import Mangum
    settings = Settings.from_env()
    # Function URL's trusted request context supplies the deployment hostname;
    # the SDK still checks the caller's Host header against this exact value.
    domain = event.get("requestContext", {}).get("domainName", "")
    if re.fullmatch(r"[a-z0-9]+\.lambda-url\.[a-z0-9-]+\.on\.aws", domain):
        settings = replace(settings, allowed_hosts=(*settings.allowed_hosts, domain))
    return Mangum(create_app(settings), lifespan="on")(event, context)
