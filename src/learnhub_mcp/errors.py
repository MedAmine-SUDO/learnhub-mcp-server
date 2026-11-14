class LearnHubError(Exception):
    """Base error. The message is safe to show to the AI client."""


class AuthError(LearnHubError):
    """Client token missing, unknown, or rejected by the upstream OAuth server."""


class NotFoundError(LearnHubError):
    """Upstream resource does not exist (or belongs to another tenant)."""


class UpstreamError(LearnHubError):
    """Upstream API failed after retries, or returned an unexpected response."""


class ToolInputError(LearnHubError):
    """Tool arguments failed validation."""
