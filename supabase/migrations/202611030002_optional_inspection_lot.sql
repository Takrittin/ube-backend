BEGIN;
ALTER TABLE public.inspections ALTER COLUMN lot_id DROP NOT NULL;
UPDATE public.alembic_version SET version_num = '0002_optional_inspection_lot'
WHERE version_num = '0001_inspection_schema';
COMMIT;
