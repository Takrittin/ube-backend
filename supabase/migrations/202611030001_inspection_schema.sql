-- Generated from the frozen 0001_inspection_schema Alembic revision.
-- Run with Supabase SQL Editor OR python -m app.migrate; both adopt existing starter tables.
BEGIN;

CREATE TABLE IF NOT EXISTS products (
	id VARCHAR(36) NOT NULL, 
	sku VARCHAR(80) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	bag_type VARCHAR(20) DEFAULT 'unknown' NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_products_sku ON products (sku);

CREATE TABLE IF NOT EXISTS users (
	id VARCHAR(36) NOT NULL, 
	username VARCHAR(80) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	full_name VARCHAR(120) DEFAULT '' NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username);

CREATE TABLE IF NOT EXISTS lots (
	id VARCHAR(36) NOT NULL, 
	lot_code VARCHAR(100) NOT NULL, 
	product_id VARCHAR(36) NOT NULL, 
	production_date VARCHAR(10), 
	quantity INTEGER DEFAULT '0' NOT NULL, 
	status VARCHAR(20) DEFAULT 'open' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_lots_lot_code ON lots (lot_code);

CREATE TABLE IF NOT EXISTS inspections (
	id VARCHAR(36) NOT NULL, 
	lot_id VARCHAR(36) NOT NULL, 
	inspector_id VARCHAR(36) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	pallet_code VARCHAR(100) DEFAULT '' NOT NULL, 
	material_code VARCHAR(100) DEFAULT '' NOT NULL, 
	qr_raw TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lot_id) REFERENCES lots (id), 
	FOREIGN KEY(inspector_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_inspections_lot_id ON inspections (lot_id);

CREATE TABLE IF NOT EXISTS ai_results (
	id VARCHAR(36) NOT NULL, 
	inspection_id VARCHAR(36) NOT NULL, 
	decision VARCHAR(20) NOT NULL, 
	defect_type VARCHAR(100), 
	confidence FLOAT NOT NULL, 
	measurements JSON NOT NULL, 
	model_version VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (inspection_id), 
	FOREIGN KEY(inspection_id) REFERENCES inspections (id)
);

CREATE TABLE IF NOT EXISTS final_results (
	id VARCHAR(36) NOT NULL, 
	inspection_id VARCHAR(36) NOT NULL, 
	decided_by VARCHAR(36) NOT NULL, 
	decision VARCHAR(20) NOT NULL, 
	notes TEXT, 
	override_reason TEXT, 
	confirmed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (inspection_id), 
	FOREIGN KEY(inspection_id) REFERENCES inspections (id), 
	FOREIGN KEY(decided_by) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS inspection_images (
	id VARCHAR(36) NOT NULL, 
	inspection_id VARCHAR(36) NOT NULL, 
	stored_path VARCHAR(512) NOT NULL, 
	uploaded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (inspection_id), 
	FOREIGN KEY(inspection_id) REFERENCES inspections (id)
);

ALTER TABLE public."users" ADD COLUMN IF NOT EXISTS full_name VARCHAR(120) DEFAULT '' NOT NULL;

ALTER TABLE public."products" ADD COLUMN IF NOT EXISTS bag_type VARCHAR(20) DEFAULT 'unknown' NOT NULL;

ALTER TABLE public."lots" ADD COLUMN IF NOT EXISTS production_date VARCHAR(10);

ALTER TABLE public."lots" ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE public."lots" ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'open' NOT NULL;

ALTER TABLE public."inspections" ADD COLUMN IF NOT EXISTS pallet_code VARCHAR(100) DEFAULT '' NOT NULL;

ALTER TABLE public."inspections" ADD COLUMN IF NOT EXISTS material_code VARCHAR(100) DEFAULT '' NOT NULL;

ALTER TABLE public."inspections" ADD COLUMN IF NOT EXISTS qr_raw TEXT;

CREATE TABLE IF NOT EXISTS public.alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);

INSERT INTO public.alembic_version (version_num) VALUES ('0001_inspection_schema') ON CONFLICT DO NOTHING;

ALTER TABLE public."users" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."users" FROM anon, authenticated;

ALTER TABLE public."products" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."products" FROM anon, authenticated;

ALTER TABLE public."lots" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."lots" FROM anon, authenticated;

ALTER TABLE public."inspections" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."inspections" FROM anon, authenticated;

ALTER TABLE public."inspection_images" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."inspection_images" FROM anon, authenticated;

ALTER TABLE public."ai_results" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."ai_results" FROM anon, authenticated;

ALTER TABLE public."final_results" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."final_results" FROM anon, authenticated;

ALTER TABLE public."alembic_version" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public."alembic_version" FROM anon, authenticated;

COMMIT;
