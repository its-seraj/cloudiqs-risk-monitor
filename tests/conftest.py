"""Pytest global fixtures and configuration."""

from unittest.mock import MagicMock
import httpx
import pytest

@pytest.fixture(autouse=True)
def mock_httpx_post_by_default(monkeypatch, request):
    """Speed up unit tests by mocking httpx.Client.post unless test overrides it."""
    # Don't override if test specifically tests retry failure
    if "retry_on_failure" in request.node.name:
        return

    # Default mock response for fast local tests
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.is_success = True
    mock_response.status_code = 200
    mock_response.text = '{"status": "received"}'
    
    monkeypatch.setattr(httpx.Client, "post", lambda self, *args, **kwargs: mock_response)
