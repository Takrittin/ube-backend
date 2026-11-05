BEGIN;
CREATE TABLE IF NOT EXISTS public.geometry_measurements (
    id VARCHAR(36) PRIMARY KEY,
    inspection_id VARCHAR(36) NOT NULL UNIQUE REFERENCES public.inspections(id),
    measured_by VARCHAR(36) NOT NULL REFERENCES public.users(id),
    annotation JSON NOT NULL,
    report JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE public.geometry_measurements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.geometry_measurements FROM anon, authenticated;
UPDATE public.alembic_version SET version_num = '0004_geometry_measurements'
WHERE version_num = '0003_human_defects';
COMMIT;
