"""The `org-harvest` command-line entry point.

Each story that adds a runnable capability adds a subcommand here. Story 1
establishes the shared credential options (AC-3.8) and a minimal `auth-check`
command that exercises them end to end.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import click

from org_harvest.credentials import CredentialProvider, build_credential_provider
from org_harvest.errors import OrgHarvestError
from org_harvest.hosts import ApiHost


def _credential_options[**P, R](f: Callable[P, R]) -> Callable[P, R]:
    """Shared credential/host options for every command that talks to GitHub
    (AC-3.8): explicit arguments, or the documented environment variables for
    CI. A private key is always a file path, never inline (FR-2)."""
    f = click.option(
        "--app-private-key-path",
        envvar="ORG_HARVEST_APP_PRIVATE_KEY_PATH",
        default=None,
        help="Path to the GitHub App's PEM private key.",
    )(f)
    f = click.option(
        "--app-client-id",
        envvar="ORG_HARVEST_APP_CLIENT_ID",
        default=None,
        help="The GitHub App's client ID.",
    )(f)
    f = click.option(
        "--token",
        envvar="ORG_HARVEST_TOKEN",
        default=None,
        help="A pre-minted installation access token, as an alternative to "
        "--app-private-key-path/--app-client-id.",
    )(f)
    f = click.option(
        "--api-host",
        envvar="ORG_HARVEST_API_HOST",
        default="github.com",
        show_default=True,
        help="API host: github.com, a GHEC data-residency host (api.<tenant>.ghe.com), "
        "or a GitHub Enterprise Server hostname.",
    )(f)
    return f


def _warn_if_token_on_command_line(ctx: click.Context) -> None:
    source = ctx.get_parameter_source("token")
    if source is click.core.ParameterSource.COMMANDLINE:
        click.echo(
            "warning: --token was passed on the command line and is visible "
            "in process listings; prefer the ORG_HARVEST_TOKEN environment "
            "variable in CI.",
            err=True,
        )


def _run_async[R](coro: Coroutine[Any, Any, R]) -> R:
    return asyncio.run(coro)


@click.group()
def main() -> None:
    """Download a complete GitHub organization snapshot via GraphQL."""


@main.command("auth-check")
@click.argument("org")
@_credential_options
@click.pass_context
def auth_check(
    ctx: click.Context,
    org: str,
    app_private_key_path: str | None,
    app_client_id: str | None,
    token: str | None,
    api_host: str,
) -> None:
    """Authenticate against ORG and report the resulting installation."""
    _warn_if_token_on_command_line(ctx)
    try:
        provider = build_credential_provider(
            private_key_path=app_private_key_path,
            client_id=app_client_id,
            token=token,
            org=org,
            api_host=ApiHost(api_host),
        )
        _run_async(_do_auth_check(provider))
    except OrgHarvestError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


async def _do_auth_check(provider: CredentialProvider) -> None:
    try:
        await provider.get_token()
        if provider.installation_id is not None:
            click.echo(f"authenticated: installation id {provider.installation_id}")
        else:
            click.echo("authenticated: using pre-minted token")
    finally:
        await provider.aclose()


if __name__ == "__main__":
    main()
