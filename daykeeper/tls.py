"""The TLS context of every HTTPS request the app makes (the update check, GitLab, Jira).

Everywhere but a built macOS app this is Python's default context: the system's
certificate store. A frozen macOS build has no store Python can read (the python.org
build looks for an OpenSSL folder that a bundle does not carry), so there the context
trusts the CA list of certifi, which the build carries (requirements-build.txt)."""

from __future__ import annotations

import ssl
import sys


def context(platform: str = sys.platform, frozen: bool | None = None) -> ssl.SSLContext:
    """A verifying client context; on a frozen macOS build with certifi's CA bundle."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen and platform == "darwin":
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()
