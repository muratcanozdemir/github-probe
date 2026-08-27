"""Resolves REST and GraphQL base URLs for a configurable API host (AC-3.9).

Three shapes are supported:
  - "github.com" (the default): the public api.github.com endpoints.
  - A hostname already starting with "api.": treated as already being an API
    host and used as-is. This covers GitHub Enterprise Cloud data-residency
    tenants, whose API lives at e.g. api.octocorp.ghe.com.
  - Any other hostname: treated as a GitHub Enterprise Server appliance,
    whose REST and GraphQL APIs are published under /api/v3 and /api/graphql.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiHost:
    host: str = "github.com"

    @property
    def rest_base_url(self) -> str:
        if self.host == "github.com":
            return "https://api.github.com"
        if self.host.startswith("api."):
            return f"https://{self.host}"
        return f"https://{self.host}/api/v3"

    @property
    def graphql_url(self) -> str:
        if self.host == "github.com":
            return "https://api.github.com/graphql"
        if self.host.startswith("api."):
            return f"https://{self.host}/graphql"
        return f"https://{self.host}/api/graphql"
