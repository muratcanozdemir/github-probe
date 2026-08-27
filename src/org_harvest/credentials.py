"""GitHub App installation authentication (US-3).

Two credential forms are supported, both implementing `CredentialProvider`:

- `AppKeyCredentialProvider`: given a private key and client ID, discovers the
  installation for an org, mints an installation token, and refreshes it
  before it expires (AC-3.1, AC-3.3).
- `StaticTokenCredentialProvider`: wraps a pre-minted token as-is, never
  refreshes it, and reports itself as non-refreshable so callers can react
  appropriately when it eventually expires (AC-3.2, AC-3.4).

Bootstrap requests here (installation discovery, token minting) use their own
small retry loop rather than the shared Transport built in Story 2, to avoid
a circular dependency: Transport is *parameterized by* a CredentialProvider
(architecture.md, Decision 3), so the provider must be usable on its own.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn, Protocol, runtime_checkable

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from org_harvest.constants import REST_API_VERSION, USER_AGENT
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.hosts import ApiHost

#: Refresh an App-minted token once its remaining lifetime drops below this,
#: rather than waiting until it actually expires (AC-3.3).
_REFRESH_MARGIN_SECONDS = 300.0

#: JWT clock-drift protection: back-date `iat` by this much (per GitHub's
#: documented guidance) and keep `exp` safely under the 10-minute ceiling.
_JWT_IAT_SKEW_SECONDS = 60
_JWT_EXP_SECONDS = 9 * 60

_BOOTSTRAP_MAX_ATTEMPTS = 3
_BOOTSTRAP_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@runtime_checkable
class CredentialProvider(Protocol):
    """Supplies a valid bearer token for authenticating to the GitHub API."""

    #: The installation this provider authenticates as, once known. `None`
    #: for a static token, which carries no installation metadata.
    installation_id: int | None

    #: The permissions actually granted to the current token, keyed by
    #: GitHub's App permission names (e.g. "contents", "members"), or `None`
    #: when unknown. A pre-minted token's permissions are never introspectable
    #: locally — GitHub has no "what can this token do" endpoint — so this is
    #: always `None` for `StaticTokenCredentialProvider` (AC-6.1).
    permissions: dict[str, str] | None

    #: "all" or "selected", or `None` when unknown (same caveat as
    #: `permissions`) (AC-6.1, EC-3).
    repository_selection: str | None

    async def get_token(self) -> str:
        """Return a currently-valid installation token, minting or
        refreshing it first if needed."""
        ...

    def can_refresh(self) -> bool:
        """Whether this provider can obtain a new token once the current one
        is rejected or about to expire."""
        ...

    def seconds_until_expiry(self) -> float | None:
        """Seconds until the current token expires, or `None` if unknown."""
        ...

    async def aclose(self) -> None:
        """Release any resources (HTTP connections) this provider holds."""
        ...


class StaticTokenCredentialProvider:
    """Wraps a pre-minted installation token. Never refreshes (AC-3.2).

    `expires_at` is optional: GitHub's token-minting response includes it,
    so a caller who minted the token themselves (or otherwise knows it) can
    pass it along so the transport layer's rate-limit-wait safety check
    (AC-7.4) can actually protect them. Without it, expiry is simply unknown
    until GitHub rejects the token (AC-3.4) — this class does not guess.
    """

    def __init__(self, token: str, *, expires_at: float | None = None) -> None:
        if not token or not token.strip():
            raise OrgHarvestError("Pre-minted token is empty.", kind=ErrorKind.CREDENTIAL_INVALID)
        self._token = token
        self._expires_at = expires_at
        self.installation_id: int | None = None
        self.permissions: dict[str, str] | None = None
        self.repository_selection: str | None = None

    async def get_token(self) -> str:
        return self._token

    def can_refresh(self) -> bool:
        return False

    def seconds_until_expiry(self) -> float | None:
        if self._expires_at is None:
            return None  # Unknown until GitHub rejects it (AC-3.4).
        return self._expires_at - time.time()

    async def aclose(self) -> None:
        return None


class AppKeyCredentialProvider:
    """Mints and refreshes installation tokens from a GitHub App private key
    (AC-3.1, AC-3.3, AC-3.9)."""

    def __init__(
        self,
        *,
        private_key_path: str | Path,
        client_id: str,
        org: str,
        api_host: ApiHost | None = None,
    ) -> None:
        if not client_id or not client_id.strip():
            raise OrgHarvestError("App client ID is empty.", kind=ErrorKind.CREDENTIAL_INVALID)
        self._client_id = client_id
        self._org = org
        self._host = api_host or ApiHost()
        self._private_key_pem = _load_private_key_pem(private_key_path)
        self._http = httpx.AsyncClient(timeout=30.0)

        self._token: str | None = None
        self._expires_at: float | None = None
        self.installation_id: int | None = None
        self.repository_selection: str | None = None
        self.permissions: dict[str, str] | None = None

    def can_refresh(self) -> bool:
        return True

    def seconds_until_expiry(self) -> float | None:
        if self._expires_at is None:
            return None
        return self._expires_at - time.time()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_token(self) -> str:
        remaining = self.seconds_until_expiry()
        if self._token is None or remaining is None or remaining < _REFRESH_MARGIN_SECONDS:
            await self._refresh()
        assert self._token is not None
        return self._token

    async def _refresh(self) -> None:
        app_jwt = self._build_jwt()
        if self.installation_id is None:
            await self._discover_installation(app_jwt)
        await self._mint_token(app_jwt)

    def _build_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - _JWT_IAT_SKEW_SECONDS,
            "exp": now + _JWT_EXP_SECONDS,
            "iss": self._client_id,
        }
        return jwt.encode(payload, self._private_key_pem, algorithm="RS256")

    async def _discover_installation(self, app_jwt: str) -> None:
        url = f"{self._host.rest_base_url}/orgs/{self._org}/installation"
        resp = await _bootstrap_request(self._http, "GET", url, app_jwt)
        if resp.status_code == 401:
            raise OrgHarvestError(
                "Authenticating as the GitHub App failed. Either the private "
                "key does not match the given client ID, or the host clock "
                "is skewed (JWT timestamps must be accurate — consider NTP).",
                kind=ErrorKind.CREDENTIAL_INVALID,
            )
        if resp.status_code == 404:
            await self._raise_org_or_installation_not_found(app_jwt)
        resp.raise_for_status()
        data = resp.json()
        self.installation_id = data["id"]
        self.repository_selection = data.get("repository_selection")

    async def _raise_org_or_installation_not_found(self, app_jwt: str) -> NoReturn:
        url = f"{self._host.rest_base_url}/orgs/{self._org}"
        resp = await _bootstrap_request(self._http, "GET", url, app_jwt)
        if resp.status_code == 404:
            raise OrgHarvestError(
                f"Organization '{self._org}' does not exist.",
                kind=ErrorKind.ORG_NOT_FOUND,
            )
        raise OrgHarvestError(
            f"The GitHub App is not installed on organization '{self._org}'.",
            kind=ErrorKind.APP_NOT_INSTALLED,
        )

    async def _mint_token(self, app_jwt: str) -> None:
        url = f"{self._host.rest_base_url}/app/installations/{self.installation_id}/access_tokens"
        resp = await _bootstrap_request(self._http, "POST", url, app_jwt)
        if resp.status_code in (401, 403, 404):
            raise OrgHarvestError(
                "Minting an installation token failed. The installation may "
                "have been suspended or uninstalled, or its private key "
                "revoked, since it was last used.",
                kind=ErrorKind.AUTH_FAILED,
            )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        self._expires_at = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        ).timestamp()
        self.repository_selection = data.get("repository_selection", self.repository_selection)
        self.permissions = data.get("permissions")


def _load_private_key_pem(path: str | Path) -> bytes:
    resolved = Path(path)
    if not resolved.exists():
        raise OrgHarvestError(
            f"Private key file does not exist: {resolved}",
            kind=ErrorKind.CREDENTIAL_INVALID,
        )
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise OrgHarvestError(
            f"Private key file is unreadable: {resolved} ({exc})",
            kind=ErrorKind.CREDENTIAL_INVALID,
        ) from exc

    try:
        load_pem_private_key(data, password=None)
    except TypeError as exc:
        raise OrgHarvestError(
            f"Private key at {resolved} is passphrase-protected; "
            "org-harvest requires an unencrypted PEM.",
            kind=ErrorKind.CREDENTIAL_INVALID,
        ) from exc
    except ValueError as exc:
        raise OrgHarvestError(
            f"Private key at {resolved} is not a valid PEM private key.",
            kind=ErrorKind.CREDENTIAL_INVALID,
        ) from exc
    return data


async def _bootstrap_request(
    http: httpx.AsyncClient, method: str, url: str, app_jwt: str
) -> httpx.Response:
    """Small retry loop for the low-frequency auth bootstrap calls (installation
    discovery, token minting) — not the full pacing/backoff machinery Story 2
    builds for the high-volume data-fetch path."""
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": REST_API_VERSION,
        "User-Agent": USER_AGENT,
    }
    last_exc: Exception | None = None
    for attempt in range(_BOOTSTRAP_MAX_ATTEMPTS):
        try:
            resp = await http.request(method, url, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        else:
            if resp.status_code not in _BOOTSTRAP_RETRYABLE_STATUS:
                return resp
            last_exc = OrgHarvestError(
                f"retryable status {resp.status_code} from {url}",
                kind=ErrorKind.AUTH_FAILED,
            )
        if attempt < _BOOTSTRAP_MAX_ATTEMPTS - 1:
            await asyncio.sleep((2**attempt) * 0.5 + random.uniform(0, 0.25))
    raise OrgHarvestError(
        f"Request to {url} failed after {_BOOTSTRAP_MAX_ATTEMPTS} attempts: {last_exc}",
        kind=ErrorKind.AUTH_FAILED,
    ) from last_exc


def build_credential_provider(
    *,
    private_key_path: str | Path | None,
    client_id: str | None,
    token: str | None,
    org: str,
    api_host: ApiHost | None = None,
) -> CredentialProvider:
    """Builds the right provider from CLI/library inputs, enforcing that
    exactly one credential form is supplied (AC-3.6)."""
    key_form_given = private_key_path is not None or client_id is not None
    token_form_given = token is not None

    if key_form_given and token_form_given:
        raise OrgHarvestError(
            "Supply either a private key and client ID, or a pre-minted token — not both.",
            kind=ErrorKind.CREDENTIAL_CONFLICT,
        )
    if not key_form_given and not token_form_given:
        raise OrgHarvestError(
            "No credentials supplied. Provide either a private key and "
            "client ID, or a pre-minted installation token.",
            kind=ErrorKind.CREDENTIAL_CONFLICT,
        )
    if token_form_given:
        assert token is not None
        return StaticTokenCredentialProvider(token)

    if private_key_path is None or client_id is None:
        raise OrgHarvestError(
            "Both a private key path and a client ID are required to "
            "authenticate as a GitHub App installation.",
            kind=ErrorKind.CREDENTIAL_CONFLICT,
        )
    return AppKeyCredentialProvider(
        private_key_path=private_key_path,
        client_id=client_id,
        org=org,
        api_host=api_host,
    )


def raise_on_unauthorized(provider: CredentialProvider) -> NoReturn:
    """Called by the transport layer (Story 2) when a request is rejected as
    unauthorized. Defines what that means for each credential form."""
    if not provider.can_refresh():
        raise OrgHarvestError(
            "The installation token was rejected (it has likely expired). "
            "Pre-minted tokens are not refreshed automatically; supply a "
            "private key and client ID instead to enable automatic refresh.",
            kind=ErrorKind.AUTH_EXPIRED,
        )
    raise OrgHarvestError(
        "The installation token was rejected even though it should be "
        "refreshable. The installation may have been suspended or "
        "uninstalled, or its private key revoked, since the last refresh.",
        kind=ErrorKind.AUTH_FAILED,
    )
