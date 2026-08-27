# Deviations from Plan: org-harvest

**Last Updated:** 2026-08-28

Track any changes from the per-story implementation plan. Each deviation is logged with reasoning.

### DEV-1: Extended Story 1's `StaticTokenCredentialProvider` with an optional known expiry
**Story:** Story 3
**Planned:** Implement AC-7.4 ("does not begin a wait that would outlast the remaining credential lifetime when it cannot refresh") purely in `transport.py`, reading `credentials.seconds_until_expiry()` as Story 1 already defined it.
**Actual:** `StaticTokenCredentialProvider` (Story 1) gained an optional `expires_at` constructor parameter. Without it, `seconds_until_expiry()` still returns `None` exactly as Story 1 built it — behavior for every existing caller and test is unchanged.
**Reasoning:** `can_refresh()` is `False` only for `StaticTokenCredentialProvider`; `AppKeyCredentialProvider` always refreshes itself, so it never needs this protection. But `StaticTokenCredentialProvider.seconds_until_expiry()` always returned `None` (unknown), which means the AC-7.4 safety check in `transport.py` — which only fires when `remaining_credential is not None` — could *never* trigger for the one credential type the AC exists to protect. The check as originally planned would have been structurally present but functionally inert. Since a pre-minted token's expiry is known to whoever minted it (GitHub's mint response includes `expires_at`), exposing it as an optional parameter lets a caller who has it opt into real protection, while a caller who doesn't gets the exact prior behavior (reactive detection on a 401, per AC-3.4).
**Impact:** None on Story 1's existing tests or behavior (new parameter defaults to `None`). Story 15 (library API) should mention this option when documenting programmatic use of a pre-minted token. No CLI flag was added for it in Story 1 — deliberately out of scope for this fix, since the spec's AC-3.8 doesn't call for one; a CLI flag can be added later without breaking this design if it turns out to be wanted.

### DEV-2: Extended `CredentialProvider` protocol with `permissions`/`repository_selection`
**Story:** Story 4
**Planned:** Preflight (AC-6.1) determines "which permissions the active token carries and whether the installation is scoped to selected repositories" against whichever `CredentialProvider` it's given.
**Actual:** Promoted `permissions: dict[str, str] | None` and `repository_selection: str | None` from `AppKeyCredentialProvider`-only attributes (Story 1) to `CredentialProvider` Protocol members, with `StaticTokenCredentialProvider` now setting both to `None`.
**Reasoning:** Preflight needs to read these fields generically across both credential forms without `isinstance` branching or `hasattr`/`type: ignore` workarounds. GitHub genuinely has no endpoint to introspect a pre-minted token's granted permissions, so `None` for the static-token case isn't a gap in this story's implementation — it's a real API limitation, which preflight surfaces honestly ("permissions unknown for a pre-minted token") rather than pretending to verify what it can't.
**Impact:** None on Story 1's tests (both new fields already existed on `AppKeyCredentialProvider` from Story 1; only `StaticTokenCredentialProvider` and the Protocol declaration changed, and `StaticTokenCredentialProvider`'s existing tests don't inspect these fields).
