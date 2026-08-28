"""The `org-harvest` command-line entry point.

Each story that adds a runnable capability adds a subcommand here. Story 1
establishes the shared credential options (AC-3.8) and a minimal `auth-check`
command that exercises them end to end.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import click

from org_harvest.credentials import CredentialProvider, build_credential_provider
from org_harvest.datasets import all_specs
from org_harvest.errors import OrgHarvestError
from org_harvest.hosts import ApiHost
from org_harvest.manifest import CompletionStatus
from org_harvest.preflight import PreflightReport, Verdict, run_preflight
from org_harvest.retry import RetryResult, retry_gaps
from org_harvest.run import ExitStatus, RunResult, exit_status_for_error, run_snapshot
from org_harvest.selection import RepositoryFilter, resolve_dataset_selection
from org_harvest.transport import Transport

#: Default snapshot root (AC-1.6) — relative to the current working
#: directory, matching how the tool is documented to be invoked.
_DEFAULT_SNAPSHOT_ROOT = "snapshots"


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


def _split_csv_option(value: str | None) -> tuple[str, ...] | None:
    """Splits a comma-separated CLI option into a tuple of trimmed,
    non-empty names, or `None` when the option wasn't given at all —
    distinct from an empty tuple, which `resolve_dataset_selection()`
    (AC-2.5) and `--repos` both treat as "nothing to restrict to"."""
    if value is None:
        return None
    return tuple(name.strip() for name in value.split(",") if name.strip())


@main.group("datasets")
def datasets_group() -> None:
    """Inspect available datasets."""


@datasets_group.command("list")
def datasets_list() -> None:
    """List every dataset, both tiers, with its permissions (AC-2.7)."""
    for spec in sorted(all_specs(), key=lambda s: (s.tier.value, s.level.value, s.name)):
        perms = ", ".join(spec.required_permissions)
        click.echo(
            f"{spec.name} [{spec.tier.value}/{spec.level.value}] ({perms}) - {spec.description}"
        )


@main.command("preflight")
@click.argument("org")
@click.option(
    "--datasets", default=None, help="Comma-separated dataset names (default: all default-tier)."
)
@_credential_options
@click.pass_context
def preflight(
    ctx: click.Context,
    org: str,
    datasets: str | None,
    app_private_key_path: str | None,
    app_client_id: str | None,
    token: str | None,
    api_host: str,
) -> None:
    """Report readiness for ORG without downloading anything.

    Always exits non-zero if any selected dataset is blocked (AC-6.5) — this
    command's whole purpose is diagnostic. The `run` command's own
    `--fail-fast` (Story 10) controls whether a full download aborts on the
    same condition instead of proceeding with a warning (AC-6.4)."""
    _warn_if_token_on_command_line(ctx)
    try:
        selection = resolve_dataset_selection(_split_csv_option(datasets))
        provider = build_credential_provider(
            private_key_path=app_private_key_path,
            client_id=app_client_id,
            token=token,
            org=org,
            api_host=ApiHost(api_host),
        )
        report = _run_async(
            _do_preflight(provider, org=org, dataset_names=selection.names, api_host=api_host)
        )
        if selection.auto_included:
            click.echo(f"auto-included dependencies: {', '.join(selection.auto_included)}")
        _print_preflight_report(report)
        if report.any_blocked:
            sys.exit(1)
    except OrgHarvestError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


async def _do_preflight(
    provider: CredentialProvider, *, org: str, dataset_names: tuple[str, ...], api_host: str
) -> PreflightReport:
    transport = Transport(provider)
    try:
        return await run_preflight(
            transport, provider, org=org, dataset_names=dataset_names, api_host=ApiHost(api_host)
        )
    finally:
        await transport.aclose()
        await provider.aclose()


def _print_preflight_report(report: PreflightReport) -> None:
    if report.repository_count is not None:
        click.echo(f"organization: {report.org} ({report.repository_count} repositories)")
    if report.scope_restricted:
        click.echo("warning: installation is scoped to selected repositories, not all")
    for v in report.dataset_verdicts:
        marker = {Verdict.READY: "ready", Verdict.DEGRADED: "degraded", Verdict.BLOCKED: "blocked"}[
            v.verdict
        ]
        suffix = f" ({v.reason})" if v.reason else ""
        click.echo(f"  {v.dataset}: {marker}{suffix}")
    if report.estimated_points is not None:
        click.echo(f"estimated points: {report.estimated_points}")
    if report.estimated_duration_seconds is not None and report.estimated_duration_seconds > 0:
        click.echo(f"estimated additional wait: {report.estimated_duration_seconds:.0f}s")


@main.command("run")
@click.argument("org")
@click.option(
    "--snapshot-root",
    default=_DEFAULT_SNAPSHOT_ROOT,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory under which <org>/<timestamp>/ snapshots are written (AC-1.6).",
)
@click.option(
    "--fail-fast",
    is_flag=True,
    default=False,
    help="Abort before downloading anything if preflight finds a blocked dataset, "
    "instead of proceeding and recording it as a gap (AC-6.4).",
)
@click.option(
    "--datasets",
    default=None,
    help="Comma-separated dataset names (default: all default-tier). Naming an "
    "optional dataset enables it; a dataset's dependencies are included "
    "automatically (AC-2.1, AC-2.3, AC-2.6).",
)
@click.option(
    "--repos",
    default=None,
    help="Comma-separated repository names to restrict the run to (AC-2.8).",
)
@click.option(
    "--exclude-archived",
    is_flag=True,
    default=False,
    help="Exclude archived repositories from the run (AC-2.8).",
)
@click.option(
    "--exclude-forks",
    is_flag=True,
    default=False,
    help="Exclude forked repositories from the run (AC-2.8).",
)
@click.option(
    "--max-items-per-collection",
    type=int,
    default=None,
    help="Cap the number of items collected per repository-level collection (AC-2.9).",
)
@click.option(
    "--resume",
    default=None,
    metavar="SNAPSHOT",
    help="Resume a specific snapshot by name (its timestamp directory) instead of "
    "the newest incomplete one (AC-4.3). Ignored if --force-fresh is given.",
)
@click.option(
    "--force-fresh",
    is_flag=True,
    default=False,
    help="Start a brand-new snapshot even if an incomplete one exists for this "
    "org (AC-4.7), instead of resuming it automatically.",
)
@click.option(
    "--stale-after-days",
    type=float,
    default=7.0,
    show_default=True,
    help="Refuse to resume a snapshot older than this many days (AC-4.10), unless "
    "--allow-stale-resume is also given.",
)
@click.option(
    "--allow-stale-resume",
    is_flag=True,
    default=False,
    help="Resume a snapshot even if it's older than --stale-after-days (AC-4.10).",
)
@_credential_options
@click.pass_context
def run(
    ctx: click.Context,
    org: str,
    snapshot_root: Path,
    fail_fast: bool,
    datasets: str | None,
    repos: str | None,
    exclude_archived: bool,
    exclude_forks: bool,
    max_items_per_collection: int | None,
    resume: str | None,
    force_fresh: bool,
    stale_after_days: float,
    allow_stale_resume: bool,
    app_private_key_path: str | None,
    app_client_id: str | None,
    token: str | None,
    api_host: str,
) -> None:
    """Download a complete snapshot of ORG in one command (AC-1.1)."""
    _warn_if_token_on_command_line(ctx)
    try:
        provider = build_credential_provider(
            private_key_path=app_private_key_path,
            client_id=app_client_id,
            token=token,
            org=org,
            api_host=ApiHost(api_host),
        )
    except OrgHarvestError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(exit_status_for_error(exc))

    repo_names = _split_csv_option(repos)
    repository_filter = RepositoryFilter(
        names=frozenset(repo_names) if repo_names else None,
        exclude_archived=exclude_archived,
        exclude_forks=exclude_forks,
    )

    try:
        result = _run_async(
            _do_run(
                provider,
                org=org,
                snapshot_root=snapshot_root,
                api_host=api_host,
                fail_fast=fail_fast,
                dataset_names=_split_csv_option(datasets),
                repository_filter=None if repository_filter.is_noop else repository_filter,
                item_cap=max_items_per_collection,
                resume=resume,
                force_fresh=force_fresh,
                stale_after_days=stale_after_days,
                allow_stale_resume=allow_stale_resume,
            )
        )
    except KeyboardInterrupt:
        click.echo("interrupted", err=True)
        sys.exit(ExitStatus.USER_INTERRUPT)

    _print_run_result(result)
    sys.exit(result.exit_status)


async def _do_run(
    provider: CredentialProvider,
    *,
    org: str,
    snapshot_root: Path,
    api_host: str,
    fail_fast: bool,
    dataset_names: tuple[str, ...] | None = None,
    repository_filter: RepositoryFilter | None = None,
    item_cap: int | None = None,
    resume: str | None = None,
    force_fresh: bool = False,
    stale_after_days: float = 7.0,
    allow_stale_resume: bool = False,
) -> RunResult:
    transport = Transport(provider)
    try:
        return await run_snapshot(
            transport,
            provider,
            org=org,
            snapshot_root=snapshot_root,
            api_host=ApiHost(api_host),
            fail_fast=fail_fast,
            dataset_names=dataset_names,
            repository_filter=repository_filter,
            item_cap=item_cap,
            resume=resume,
            force_fresh=force_fresh,
            stale_after_days=stale_after_days,
            allow_stale_resume=allow_stale_resume,
        )
    finally:
        await transport.aclose()
        await provider.aclose()


def _print_run_result(result: RunResult) -> None:
    manifest = result.manifest
    if result.resumed_from is not None:
        click.echo(f"resuming snapshot: {result.resumed_from}")
    if result.reclaimed_stale_claim:
        click.echo("warning: reclaimed a stale run claim left by a terminated process (EC-12)")
    if result.auto_included_datasets:
        click.echo(f"auto-included dependencies: {', '.join(result.auto_included_datasets)}")
    if manifest is not None:
        for name, count in sorted(manifest.dataset_counts.items()):
            click.echo(f"  {name}: {count}")
        click.echo(f"elapsed: {result.elapsed_seconds:.1f}s")
        c = manifest.consumption
        click.echo(f"graphql points consumed: {c.graphql_points_consumed}")
        click.echo(f"graphql requests: {c.graphql_requests}")
        click.echo(f"rest requests consumed: {c.rest_requests_consumed}")
        click.echo(f"rate-limit waits: {c.rate_limit_waits}")
        if manifest.gaps:
            click.echo(f"completed with {len(manifest.gaps)} gap(s)")
        if manifest.scope_restricted:
            click.echo("warning: installation is scoped to selected repositories, not all")
        click.echo(f"snapshot: {result.snapshot_dir}")
    elif result.exit_status is ExitStatus.USER_INTERRUPT:
        # A graceful first interrupt (AC-4.11) is a notice, not a failure —
        # the snapshot is intact and resumable, not incomplete-by-accident.
        if result.snapshot_dir is not None:
            click.echo(f"snapshot (incomplete, resumable): {result.snapshot_dir}")
        if result.message:
            click.echo(result.message)
    else:
        if result.snapshot_dir is not None:
            click.echo(f"snapshot (incomplete): {result.snapshot_dir}", err=True)
        if result.message:
            click.echo(f"error: {result.message}", err=True)


@main.command("retry-gaps")
@click.argument("org")
@click.argument("snapshot")
@click.option(
    "--snapshot-root",
    default=_DEFAULT_SNAPSHOT_ROOT,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Root the snapshot lives under — must match the original run's --snapshot-root (AC-1.6).",
)
@_credential_options
@click.pass_context
def retry_gaps_command(
    ctx: click.Context,
    org: str,
    snapshot: str,
    snapshot_root: Path,
    app_private_key_path: str | None,
    app_client_id: str | None,
    token: str | None,
    api_host: str,
) -> None:
    """Re-attempt only the gapped resources of an existing, completed
    SNAPSHOT of ORG (AC-11.1) — SNAPSHOT is the timestamp directory name a
    prior `run` printed."""
    _warn_if_token_on_command_line(ctx)
    snapshot_dir = snapshot_root / org.lower() / snapshot
    try:
        provider = build_credential_provider(
            private_key_path=app_private_key_path,
            client_id=app_client_id,
            token=token,
            org=org,
            api_host=ApiHost(api_host),
        )
    except OrgHarvestError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(exit_status_for_error(exc))

    try:
        result = _run_async(
            _do_retry_gaps(provider, org=org, snapshot_dir=snapshot_dir, api_host=api_host)
        )
    except OrgHarvestError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(exit_status_for_error(exc))

    _print_retry_result(result)
    if not result.retried:
        sys.exit(ExitStatus.SUCCESS)
    status = (
        ExitStatus.SUCCESS
        if result.manifest.status is CompletionStatus.COMPLETE
        else ExitStatus.COMPLETED_WITH_GAPS
    )
    sys.exit(status)


async def _do_retry_gaps(
    provider: CredentialProvider, *, org: str, snapshot_dir: Path, api_host: str
) -> RetryResult:
    transport = Transport(provider)
    try:
        return await retry_gaps(
            transport, provider, org=org, snapshot_dir=snapshot_dir, api_host=ApiHost(api_host)
        )
    finally:
        await transport.aclose()
        await provider.aclose()


def _print_retry_result(result: RetryResult) -> None:
    if not result.retried:
        click.echo("no gaps to retry — snapshot is already clean")
        return
    click.echo(f"retried datasets: {', '.join(result.datasets_retried)}")
    for name, count in sorted(result.manifest.dataset_counts.items()):
        click.echo(f"  {name}: {count}")
    if result.manifest.gaps:
        click.echo(f"still {len(result.manifest.gaps)} gap(s) remaining")
    else:
        click.echo("all gaps resolved")
    click.echo(f"retried at: {result.manifest.last_retried_at}")


if __name__ == "__main__":
    main()
