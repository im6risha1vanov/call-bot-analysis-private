# Team knowledge: dormant backend foundation

This module is not registered in `crm.web` and does not run by itself. If explicitly integrated later, `CRM_TEAM_KNOWLEDGE_ENABLED=1` is also required; without it each route returns 404. Apply migration 060 only as part of a separately approved deployment. No provider sync or external URL requests are implemented.

Access uses `team_assignments.identity`: the existing pilot and active employee/invite checks apply. Data is scoped by company. Private sources and documents are accessible to their creator; shared sources and documents are available to pilot users of the same company. Only a CRM director can create a shared item. A shared document must point to a shared source; a private document may point to an accessible shared source. Source and document ownership are immutable in this foundation. No deal linkage is implemented.

If later registered as `team_knowledge.routes(viewer, body, POOL)`, endpoints are:

- `POST /api/team/knowledge/sources` with `{title, provenance_url?, provenance_check?, source_date?, scope?}`. Scope defaults to `private`; date is ISO `YYYY-MM-DD`. URL is metadata only and never fetched. `provenance_check` is the author's note, not a system verification.
- `GET /api/team/knowledge/sources` lists at most 100 accessible sources.
- `POST /api/team/knowledge/documents` with `{source_id, title, body, scope?}`. Body is at most 100,000 characters.
- `GET /api/team/knowledge/documents?q=...` lists up to 100 accessible matches with excerpts and source metadata. Empty query lists recent items; matching is case-insensitive literal substring of title/body.
- `GET /api/team/knowledge/documents/{id}` returns the complete text and source metadata only when accessible.

No update, deletion, external retrieval, automatic ingestion, semantic search or publication workflow is included. Database and API access policies must remain aligned if other consumers are added. Text is stored in PostgreSQL; configure existing database backups and access controls before activation.

Test in a disposable database only. The test validates loopback DSN and exact database name before destructive schema reset, then verifies `current_database()`:

```powershell
$env:KNOWLEDGE_TEST_DSN='postgresql://averon_test@127.0.0.1:55478/callbot_methodology_test_knowledge'
python -m unittest tests.test_team_knowledge -v
```
