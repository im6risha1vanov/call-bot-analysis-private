BEGIN;

ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS admin_version BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION crm_company_settings_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.admin_version := OLD.admin_version + 1;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS crm_company_settings_revision ON company_settings;
CREATE TRIGGER crm_company_settings_revision BEFORE UPDATE ON company_settings
    FOR EACH ROW EXECUTE FUNCTION crm_company_settings_revision();

CREATE TABLE IF NOT EXISTS crm_admin_requests (
    actor_id BIGINT NOT NULL,
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_id, request_key)
);

CREATE TABLE IF NOT EXISTS crm_admin_audit (
    id BIGSERIAL PRIMARY KEY,
    actor_id BIGINT NOT NULL,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    resource TEXT NOT NULL,
    entity TEXT NOT NULL,
    action TEXT NOT NULL,
    before_state JSONB,
    after_state JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crm_admin_audit_client_idx ON crm_admin_audit(client_id, id DESC);

CREATE TABLE IF NOT EXISTS crm_admin_role_rules (
    client_id INTEGER NOT NULL REFERENCES clients(id),
    role TEXT NOT NULL CHECK (role IN ('director', 'manager', 'client')),
    denied TEXT[] NOT NULL DEFAULT '{}',
    version BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (client_id, role)
);

CREATE TABLE IF NOT EXISTS crm_pipeline_versions (
    client_id INTEGER NOT NULL REFERENCES clients(id),
    pipeline TEXT NOT NULL,
    version BIGINT NOT NULL,
    config JSONB NOT NULL,
    published_at TIMESTAMPTZ,
    PRIMARY KEY (client_id, pipeline, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_pipeline_one_draft
    ON crm_pipeline_versions(client_id, pipeline) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_pipeline_published
    ON crm_pipeline_versions(client_id, pipeline, published_at DESC) WHERE published_at IS NOT NULL;

CREATE OR REPLACE FUNCTION crm_original_pipeline() RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
SELECT '{
  "name":"Продажи",
  "stages":[
    {"key":"base","name":"База номеров","stall_days":null},
    {"key":"cold","name":"Холодный звонок","stall_days":7},
    {"key":"presentation","name":"Презентация","stall_days":5},
    {"key":"messenger","name":"Telegram или почта","stall_days":5},
    {"key":"contract","name":"Договор","stall_days":7},
    {"key":"paid","name":"Оплата","stall_days":null}
  ],
  "reasons":{
    "expensive":"дорого","no_budget":"нет бюджета","competitor":"выбрали другого подрядчика",
    "no_need":"нет потребности","not_dmp":"не дошли до ЛПР","silent":"пропал, не отвечает",
    "terms":"не устроили условия договора","no_trust":"не поверили в результат","other":"другое"
  },
  "transitions":{}
}'::jsonb
$$;

CREATE OR REPLACE FUNCTION crm_seed_pipeline() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO crm_pipeline_versions(client_id, pipeline, version, config, published_at)
    VALUES (NEW.id, 'default', 0, crm_original_pipeline(), TIMESTAMPTZ '1970-01-01 00:00:00+00')
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS crm_seed_pipeline ON clients;
CREATE TRIGGER crm_seed_pipeline AFTER INSERT ON clients FOR EACH ROW EXECUTE FUNCTION crm_seed_pipeline();
INSERT INTO crm_pipeline_versions(client_id, pipeline, version, config, published_at)
SELECT id, 'default', 0, crm_original_pipeline(), TIMESTAMPTZ '1970-01-01 00:00:00+00' FROM clients
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS crm_deal_stage_changes (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    pipeline_version BIGINT NOT NULL,
    stage TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (client_id, phone, id),
    FOREIGN KEY (client_id, pipeline, pipeline_version)
        REFERENCES crm_pipeline_versions(client_id, pipeline, version)
);
CREATE INDEX IF NOT EXISTS crm_deal_stage_client_phone
    ON crm_deal_stage_changes(client_id, phone, id DESC);

CREATE TABLE IF NOT EXISTS crm_automation_versions (
    id BIGSERIAL PRIMARY KEY,
    automation_id BIGINT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    spec JSONB NOT NULL,
    simulated_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_automation_one_draft
    ON crm_automation_versions(automation_id) WHERE published_at IS NULL;
ALTER TABLE automations ADD COLUMN IF NOT EXISTS admin_version_id BIGINT;
ALTER TABLE automations ADD COLUMN IF NOT EXISTS admin_managed BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE automations ADD COLUMN IF NOT EXISTS admin_activated_at TIMESTAMPTZ;
ALTER TABLE automation_runs ADD COLUMN IF NOT EXISTS admin_version_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS crm_admin_automation_event_once
    ON automation_runs(automation_id, event_id) WHERE admin_version_id IS NOT NULL AND event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_admin_integrations (
    client_id INTEGER NOT NULL REFERENCES clients(id),
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_success_at TIMESTAMPTZ,
    error TEXT,
    PRIMARY KEY (client_id, kind)
);

CREATE TABLE IF NOT EXISTS crm_admin_operations (
    id BIGSERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    actor_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    input_enc TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_admin_operation_pending
    ON crm_admin_operations(client_id,kind) WHERE status IN ('queued','running');

COMMIT;
