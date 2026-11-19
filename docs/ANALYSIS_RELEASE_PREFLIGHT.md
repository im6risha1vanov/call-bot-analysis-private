# Mandatory analysis check before updates

The deployed checker is `/opt/callbot-astra-releases/check_analysis_release.py`.
It is installed outside the application checkout so a proposed release or code
rollback cannot replace the checker while being checked. Its original package
hashes are anchored to `52e4d575c03788d09194346362b1e06ef4476feb`, independently of
the candidate's manifest. The exact historical baseline remains a supported
rollback target.

Before installing a reviewed commit, run with the application runtime:

```sh
/opt/averon-crm-venvs/699c50b/bin/python \
  /opt/callbot-astra-releases/check_analysis_release.py \
  --root /opt/callbot-astra --commit FULL_COMMIT_SHA
```

Without `--commit`, the checker validates the current tracked working files,
including uncommitted edits. Package deployment scripts use `--overlay DIR
--files PATH...` to validate the proposed files over an exported current commit.

Checks performed in a temporary candidate directory:

1. Exact hashes of original `analysis.py` and its three prompts.
2. Trusted original manifest and integrity of all comparison package files.
3. Syntax of the polling, queue, handler, policy and provider modules.
4. Imports of the actual polling worker and registered `analyze_call` handler.
5. Original/current configuration resolution and trial enqueue through the real
   `_enqueue_analyze_call` and policy functions, with an in-memory fake pool,
   for both default and legacy selectors.

The child interpreter has synthetic credentials and no production settings.
Network connections, DNS lookups and child processes are blocked. It cannot call
Mango, a model or PostgreSQL; the trial task never reaches the real queue.

Any failed check exits nonzero. Deployment entry points run it before checkout,
file replacement, migrations or service shutdown/restart: the release manager,
branch deployment, CRM learning packages, review-v2 deployment, and all personal
package installers. CRM setup also checks working files before install/update.
Installed release controllers keep the gate outside the checkout. The bridge's
trusted CRM package template uses the same external checker.

For manual patches, prepare a commit in an isolated worktree, check its full SHA
with this command, then apply only that reviewed commit to production. Direct
edits or ad-hoc shell commands that bypass deployment entry points do not gain
an automatic gate. Never edit or rehash the original package to bypass a failure;
put integration/provider options in a separate transport module.

Regression coverage deliberately corrupts original source/prompts, snapshots,
manifests, handler syntax, and queue compatibility. Deployment tests assert that
rejection leaves the application commit, files, database and service state alone.

## Installation and verification, 19 November 2026

`scripts/install_analysis_release_guard.py --apply` installs the checker and
adds the preflight to existing external release controllers without replacing
their other deployment logic. It saves a restricted source backup and does not
restart any service. Installation backup:
`/opt/callbot-astra-releases/analysis-guard-20261119T110309223562Z`.

48 deployment/guard tests passed; one separate PostgreSQL test was skipped because
no disposable audit database was configured (four subtests also passed). The
current production commit and the exact historical rollback baseline both
passed the real checker.

A deliberately incompatible Git object was then checked on the server without
checking it out. Both installed release controllers rejected it before database
or service operations. Production HEAD and all worker/queue/CRM process IDs
remained unchanged, and the regular Mango poll continued successfully. No live
analysis task, paid provider request, database write or service restart was used
to perform this verification.
