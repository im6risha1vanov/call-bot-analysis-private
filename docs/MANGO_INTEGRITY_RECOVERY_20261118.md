# Mango polling recovery, 18 November 2026

## Cause and correction

Polling fetched Mango statistics successfully but failed while enqueueing
`analyze_call`: `unified.analysis_policy.verify_original()` detected changes to
the byte-pinned `analysis.py`. The CRM had added request-specific reasoning and
JSON output options to that original module.

`analysis.py` is restored byte-for-byte from the verified legacy package.
`crm/callcenter_transport.py` now owns those CRM request options. Both semantic
call verdicts and weekly operator recommendations use this transport. The
original package manifest, prompts, scoring, policy checks, and queue deduplication
remain intact. The existing Mango rate-limit test now uses the actual typed
`MangoHTTPError` and respects the configured polling interval.

## Verification

- Tested an isolated checkout of production `246a0f3`, using production's Python
  and dependencies plus separate test-only packages: 112 tests passed.
- Suites: call-center verdict, advisor, advisor jobs, insights, legacy rollback,
  and Mango polling/rate-limit tests. The request-options regression also checks
  that current and legacy configurations still pass original-package verification.
- Production code commit: `234a0450d878cda36bb6a5ca6fdfcef0a5c1c1ee`.
- Pre-deployment code backup: `/var/backups/averon-mango-integrity-20261118T112831Z`.
- Original package digest after deployment:
  `484433874b7d7a83f8c87e8daebd8fecad72ce26c01d50a85e7ffb446f594380`.
- Restarted CRM, queue runner, and polling worker. Main bot, trainer, CRM, worker,
  and queue runner were verified active.
- At 14:28 Moscow time, the normal worker fetched 286 calls from the retained
  sync window and enqueued 10 eligible calls. No sync cursor reset or history
  reprocessing was used.
- Incident call IDs: `24406`, `24426`, `24459`, `24488`, `24805`, `24827`,
  `24885`, `25301`, `25307`, `25613`.

## Completed recovery

By 14:46 Moscow time, all ten incident calls were `analyzed`, with nonempty
transcripts and report results. Their ten distinct `analyze_call` tasks were
`done`, all with zero failed attempts. The expected 27 delivery records were
`sent`; there were no failed, uncertain, sending, or pending delivery records
for these calls.

The final read-only database check found no today's calls awaiting analysis or
missing analysis tasks. Four consecutive normal worker polls succeeded after
deployment, with no polling errors or integrity exceptions; the last verified
sync was 14:43:54 Moscow time. All five application units remained active.

Calls that failed before this incident were not automatically retried by this
repair. No historical report was recomputed and no daily budget or scoring
configuration was changed.
