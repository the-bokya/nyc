"""Unit tests for proxy/caddyfile.py."""
from nyc.client.proxy.caddyfile import render


def test_empty_routes():
    assert render([]) == ""


def test_single_route():
    out = render([("a.example.com", "10.0.0.5", 80)])
    assert "a.example.com" in out
    assert "reverse_proxy 10.0.0.5:80" in out


def test_multiple_routes():
    routes = [
        ("a.example.com", "10.0.0.5", 80),
        ("b.example.com", "10.0.0.6", 8080),
    ]
    out = render(routes)
    assert "a.example.com" in out
    assert "b.example.com" in out
    assert "reverse_proxy 10.0.0.5:80" in out
    assert "reverse_proxy 10.0.0.6:8080" in out


def test_output_ends_with_newline():
    out = render([("a.example.com", "10.0.0.5", 80)])
    assert out.endswith("\n")
