BEGIN;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS email varchar(254);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS created_at timestamptz;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS last_active_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON public.users (email);
UPDATE public.alembic_version SET version_num='0006_user_directory'
WHERE version_num='0005_automatic_processing';
COMMIT;
