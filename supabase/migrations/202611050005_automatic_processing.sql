BEGIN;
ALTER TABLE public.inspections ADD COLUMN IF NOT EXISTS processing_started_at timestamptz;
ALTER TABLE public.inspections ADD COLUMN IF NOT EXISTS processing_error text;
-- Existing RLS and grants stay in force. No public access is granted.
UPDATE public.alembic_version SET version_num = '0005_automatic_processing'
WHERE version_num = '0004_geometry_measurements';
COMMIT;
