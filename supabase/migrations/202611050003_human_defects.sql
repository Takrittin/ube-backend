BEGIN;
ALTER TABLE public.final_results ADD COLUMN IF NOT EXISTS defects JSON NOT NULL DEFAULT '[]';
UPDATE public.alembic_version SET version_num = '0003_human_defects'
WHERE version_num = '0002_optional_inspection_lot';
COMMIT;
