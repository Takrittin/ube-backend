# ใช้ Supabase กับ UBE

โครงสร้าง: Next.js → `/api/backend/*` → FastAPI → Supabase PostgreSQL

ใช้ระบบบัญชีและ JWT ของ FastAPI เดิม ไม่มีการใช้ Supabase Auth ในรอบนี้ รูปภาพยังเก็บใน `uploads` ของ Backend และส่งผ่าน API ที่ตรวจสิทธิ์ ส่วน Supabase เก็บข้อมูลสินค้า ล็อต ผู้ใช้ และผลการตรวจ

## 1. สร้างโปรเจกต์ Supabase

สร้างโปรเจกต์ในบัญชีของคุณ จากนั้นเปิด **Connect → Session pooler** คัดลอก URI พร้อม host, username และ port จริง แล้วแทนค่ารหัสผ่านฐานข้อมูล อักขระพิเศษในรหัสผ่านต้อง percent-encode (อย่า encode ทั้ง URI)

แก้เฉพาะ `DATABASE_URL` ใน `.env` ของ Backend โดยรักษาค่าลับอื่นที่ตั้งไว้:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
```

รองรับ URI ที่เริ่มด้วย `postgresql://` จาก Dashboard ด้วย โปรแกรมจะแปลงเป็นไดรเวอร์ psycopg 3 ให้อัตโนมัติ ห้ามใส่ URI นี้ใน Frontend, `NEXT_PUBLIC_*`, Git หรือแชต

`sslmode=require` บังคับเข้ารหัส หากใช้จริงให้ดาวน์โหลด CA certificate จาก Supabase และใช้ `sslmode=verify-full&sslrootcert=/absolute/path/to/certificate.crt` เพื่อตรวจตัวตนของเซิร์ฟเวอร์ด้วย

## 2. ติดตั้งและสร้างตาราง

รันในโฟลเดอร์ `ube-backend-starter`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.migrate
python -m app.seed
uvicorn app.main:app --reload
```

ต้องมี `JWT_SECRET_KEY` ที่สุ่มอย่างปลอดภัย และ `SEED_ADMIN_PASSWORD` อย่างน้อย 10 ตัวอักษรใน `.env` ก่อนรัน seed ไม่มีรหัสผ่านเริ่มต้นที่ฝังในโค้ด บัญชีเดิมจะไม่ถูกเปลี่ยนรหัสผ่านเมื่อ seed ซ้ำ

Migration สร้าง 7 ตารางของแอป และ `alembic_version` พร้อมเพิ่มคอลัมน์ใหม่ให้ฐานข้อมูล starter เดิมโดยไม่ลบข้อมูล ฐานข้อมูล PostgreSQL จะไม่สร้างตารางจากการเปิดเซิร์ฟเวอร์ ให้รัน migration ก่อนทุก deployment

ทางเลือก: ใช้ SQL Editor รันไฟล์ใน `supabase/migrations/` ตามลำดับชื่อไฟล์ ตั้งแต่ `202611030001_inspection_schema.sql` จนถึง `202611100006_user_directory.sql` ให้ครบทั้ง 6 ไฟล์ เพื่อสร้าง schema ล่าสุด แล้วรัน `python -m app.seed` เพื่อสร้างบัญชี อย่าสร้างบัญชีโดยใส่รหัสผ่านธรรมดาลงตาราง

Migration เปิด RLS และถอนสิทธิ์ Data API ของ `anon` / `authenticated` จากตารางแอป รวมถึงตารางผู้ใช้ที่เก็บ password hash การอ่านและเขียนจะผ่าน FastAPI ซึ่งเชื่อมต่อด้วย trusted server database role เท่านั้น หากภายหลังใช้ role ฐานข้อมูลที่จำกัดสิทธิ์ ต้องกำหนด grants/policies เพิ่มให้เหมาะสม

ข้อมูลจาก SQLite และข้อมูลเดโมใน localStorage ไม่ได้ย้ายตามอัตโนมัติ

## 3. เปิด Frontend

รันในโฟลเดอร์ `ube-frontend`:

```bash
npm ci
cp .env.example .env.local
npm run dev
```

ค่าใน `.env.local`:

```dotenv
BACKEND_API_URL=http://127.0.0.1:8000/api/v1
```

เปิด http://localhost:3000 แล้ว Login ด้วยบัญชีที่ seed ไว้ Next.js เก็บ JWT ใน HttpOnly session cookie และส่งต่อให้ FastAPI ฝั่งเซิร์ฟเวอร์ ไม่เก็บโทเคนหรือข้อมูลการตรวจใน localStorage

ถ้า Next.js อยู่คนละเครื่องกับ FastAPI ให้เปลี่ยน `BACKEND_API_URL` เป็น URL ที่เครื่อง Next.js เข้าถึงได้ แล้ว restart Next.js ใช้ HTTPS เมื่อติดตั้งใช้งานจริง

ถ้าอยู่หลัง HTTPS reverse proxy ให้ตั้ง `APP_ORIGIN=https://โดเมนหน้าเว็บของคุณ` ใน `.env.local` ด้วย เพื่อให้ตรวจ Origin และตั้ง Secure cookie ตาม URL ที่ผู้ใช้เปิดจริง

## 4. ตรวจการเชื่อมต่อจริง

1. Login เป็น Admin
2. เปิด **ข้อมูลสินค้าและล็อต** เพิ่มสินค้าและล็อตจริงสำหรับทดสอบ
3. เปิด **ตรวจสินค้า** กรอกเลขล็อตที่มีอยู่ Pallet Code และ Material Code
4. อัปโหลดหนึ่งรูปแล้วกดวิเคราะห์ ระบบเรียกผู้ให้บริการ AI ตาม `.env`
5. ถ้าใช้ Roboflow คำขอวิเคราะห์จะใช้ inference credits ผล `REVIEW` หมายถึงผู้ตรวจต้องประเมินคุณภาพเอง
6. เลือกผ่าน/ไม่ผ่าน ใส่หมายเหตุ แล้วบันทึก
7. เปิดประวัติ รีโหลดหน้า และตรวจแถวข้อมูลใน Supabase Table Editor
8. ลอง Logout/Login ด้วยบัญชี inspector และ qa เพื่อตรวจสิทธิ์

หากต้องทดสอบโดยไม่เรียก Roboflow ให้ตั้ง `AI_PROVIDER=mock` แล้ว restart Backend ระบบจะแสดง `REVIEW` และความมั่นใจ 0 ไม่สร้างผล PASS ปลอม เมื่อทดสอบเสร็จจึงคืนค่าผู้ให้บริการที่ต้องการ

รายการ DRAFT หรือ AI_PROCESSED เปิดจากประวัติแล้วกด **ดำเนินการตรวจต่อ** ได้ การปิดใช้งานผู้ใช้เก็บบัญชีและประวัติผู้ตรวจไว้ และป้องกัน Admin ปิดบัญชีหรือลดสิทธิ์ตัวเอง

## Docker กับ Supabase

ไฟล์ `compose.yaml` เดิมใช้ PostgreSQL ภายใน Docker และเขียนทับ DATABASE_URL หากใช้ Supabase ให้ใช้ไฟล์แยกนี้:

```bash
docker compose -f compose.supabase.yaml build
docker compose -f compose.supabase.yaml run --rm api python -m app.migrate
docker compose -f compose.supabase.yaml run --rm api python -m app.seed
docker compose -f compose.supabase.yaml up -d
```

ให้คง volume ของ `uploads` ไว้ หาก deploy Backend บนเครื่องที่ไม่เก็บไฟล์ถาวร ต้องเพิ่ม object storage เช่น Supabase Storage ก่อนใช้งานรูปจริง

## การตรวจซอฟต์แวร์แบบออฟไลน์

```bash
python -m tests.smoke_flow
python -m unittest tests.test_roboflow -v
python -m unittest tests.test_integration -v
# หลัง build Frontend แล้ว:
python -m tests.web_flow
# หากมี initdb และ pg_ctl บนเครื่อง:
python -m tests.postgres_flow
```

ทดสอบเหล่านี้ใช้ฐานข้อมูลชั่วคราวและไม่เรียกบริการ AI จริง การผ่านชุดทดสอบยังไม่ยืนยันการเชื่อมต่อกับ Supabase ของคุณ ต้องทำขั้นตอนที่ 4 กับโปรเจกต์จริงด้วย

เอกสารอ้างอิง: [Database connection](https://supabase.com/docs/guides/database/connecting-to-postgres), [Data API security](https://supabase.com/docs/guides/api/securing-your-api)

## ตรวจโดยไม่ระบุล็อต

ในหน้าตรวจสินค้า กด **ข้าม — ตรวจโดยไม่ระบุล็อต** เพื่ออัปโหลดรูปได้ทันที ไม่ต้องเพิ่มสินค้า ล็อต Pallet Code หรือ Material Code รายการจะเก็บ `lot_id = NULL` และแสดงว่าไม่ระบุล็อตในประวัติและ CSV ไม่สร้างล็อตจำลอง ข้อมูลผู้ตรวจ เวลา รูป และผลตัดสินยังบันทึกตามเดิม

## รูปหลักฐานและข้อบกพร่องจากผู้ตรวจ

หน้า Review และประวัติแสดงกรอบตรวจจับจริงจาก `ai_result.measurements` พร้อมปุ่มเปิด–ปิดกรอบและขยายรูป 100–400% กรอบจะถูกซ่อนหากขนาดรูปไม่ตรงกับข้อมูลโมเดล รูปเดิมที่ไม่มีข้อมูลกรอบจะยังเปิดดูได้

ข้อบกพร่องที่ผู้ตรวจเลือกหรือกรอกเองเก็บใน `final_results.defects` แยกจาก `ai_results.defect_type` และส่งออก CSV ได้ รายการเก่าได้ค่าเริ่มต้นเป็นรายการว่าง ไม่เดาข้อบกพร่องจากหมายเหตุหรือผล AI ให้รัน `python -m app.migrate` ก่อนเปิด Backend รุ่นนี้

## ตรวจอัตโนมัติด้วย Roboflow + SAM2 + OpenCV

ติดตั้งใน environment ที่ใช้เปิด Backend แล้วเตรียมโมเดลทางการครั้งเดียว:

```bash
pip install -r requirements-sam2.txt
python -m app.prepare_sam2
python -m app.migrate
```

ใช้ `ROBOFLOW_MODEL_ID=krittin-wankundee/pallet-detection-zmufh-3-rfdetr-small-t1` ซึ่งมีคลาส `paper-bag-stack` และ `bag-layer` และตั้ง `AUTO_CHECK_ENABLED=true` ใน `.env` โมเดลที่ไม่มีสองคลาสนี้จะรายงานว่าตรวจไม่ได้ ไม่เดาชั้นจากเส้นฟิล์มหรือตัวหนังสือ ดาวน์โหลดโมเดลต้องใช้อินเทอร์เน็ต แต่ SAM2 รันในเครื่อง ไม่มีการดาวน์โหลดระหว่างการตรวจ เก็บ weights ใต้ `.model-cache/` ที่ไม่อยู่ใน Git; CLI บันทึก revision ของโมเดลด้วย ใช้ safetensors และไม่รัน remote model code

หากจัดการ schema ผ่าน SQL Editor ให้รันไฟล์ `202611050005_automatic_processing.sql` หลัง migration 0004 แทนคำสั่ง migrate; ไม่มีการเปิดสิทธิ์เพิ่มให้ browser roles

วิธีใช้: **ตรวจสินค้า → เลือกล็อตหรือข้าม → ถ่าย/อัปโหลดภาพให้เห็นเต็มกอง → ถัดไป → ดูผลอัตโนมัติ → ผู้ตรวจยืนยันผลสุดท้าย** ไม่ต้องแตะจุดหรือวาดกรอบ หน้าประวัติเปิดดูกรอบ ชั้น เส้นเอียง ค่าระหว่างชั้น และเกณฑ์เดิมที่บันทึกไว้ได้ ส่งออก CSV รวมผลอัตโนมัติและเกณฑ์ด้วย

ระบบเลือกกองหลักจากกรอบตรวจจับ นับชั้นที่พบภายในกองและตัดกรอบซ้ำ แยกขอบทั้งกองด้วย SAM2 โดยสร้างจุด prompt เอง ตรวจคุณภาพ mask แล้วใช้ OpenCV/NumPy วัดความเอียงและการเหลื่อมระหว่างชั้นที่มองเห็น บันทึกผลใน `ai_results.measurements.automatic_check` พร้อมรุ่นวิธีวัด ขนาดภาพ เกณฑ์ และ revision ของ SAM2 ไม่มีการเก็บ mask แบบเต็มในฐานข้อมูล ใช้เส้นขอบแบบย่อสำหรับ overlay

เกณฑ์ทดลองตาม notebook: **8 ชั้น, เอียงไม่เกิน 3°, ศูนย์กลางและขอบชั้นเหลื่อมไม่เกิน 4% ของความกว้างชั้นมัธยฐาน** ตั้งค่าได้ด้วย `AUTO_CHECK_EXPECTED_LAYERS`, `AUTO_CHECK_MAX_TILT_DEG`, `AUTO_CHECK_MAX_OFFSET_PERCENT`, `AUTO_CHECK_CAMERA_ROLL_DEG` ต้องตกลงเกณฑ์กับ QA และประเมินชุดภาพที่ติดป้ายกำกับก่อนใช้ในโรงงาน ไม่ใช่เกณฑ์ที่ UBE รับรอง

ผล `PASS` หมายถึงผ่านทุกการตรวจในขอบเขตนี้, `FAIL` หมายถึงมีค่าที่วัดได้เกินเกณฑ์ (การตรวจบางข้ออาจยังทำไม่ได้), `CANNOT_INSPECT` หมายถึงข้อมูลไม่พอและยังไม่มีข้อที่วัดแล้วไม่ผ่าน ระบบแปลง CANNOT_INSPECT เป็นคำแนะนำ AI REVIEW กองที่ถูกตัดขอบภาพ/ขนาดพิกัดไม่ตรงจะไม่สรุปผ่าน ความมั่นใจในช่องเดิมเป็นความมั่นใจตรวจจับวัตถุ ไม่ใช่โอกาสที่คุณภาพสินค้าผ่าน

ภาพ 2 มิติยังไม่ได้แก้ perspective และกล้องต้องตั้งตรง/สอบเทียบค่า roll ให้เหมาะสม การเหลื่อมของกรอบชั้นไม่ยืนยันการสลับฟันปลาของถุงด้านใน ความมั่นคงสามมิติ ฟิล์ม ฉลาก และรอยเสียหายยังต้องตรวจแยก ผลสุดท้ายยังบันทึกแยกเป็นการตัดสินของผู้ตรวจ

หน้าเว็บใช้ `POST /inspections/{id}/image?background=true` ซึ่งตอบ 202 หลังเก็บภาพและสถานะ PROCESSING แล้วอ่านสถานะผ่าน GET เป็นระยะ ไม่ส่งภาพหรือเรียก Roboflow ซ้ำขณะรอ เมื่อสำเร็จเป็น AI_PROCESSED; บริการล้มเหลวเป็น AI_FAILED โดยยังเก็บภาพไว้ให้กดลองใหม่ผ่าน `POST /inspections/{id}/analyze` เจ้าของรายการหรือ admin เท่านั้นที่ทำได้ แต่ละการลองใหม่อาจใช้เครดิต Roboflow เพิ่ม ไม่มีการ retry ไปคลาวด์เองโดยซ่อนจากผู้ใช้

งานเบื้องหลังใช้ FastAPI ใน process เดียว ยังไม่ใช่ durable queue: หาก server หยุดระหว่างตรวจ งานค้างจะกดลองใหม่ได้หลัง 15 นาที เปิดจากประวัติแล้วกดดำเนินการตรวจต่อ ผลจากงานเดิมที่หมดอายุจะไม่เขียนทับการลองใหม่ ควรเปลี่ยนเป็น worker/queue ที่ทนต่อการ restart ก่อนใช้หลายเครื่องหรือโหลดสูง API แบบเดิมที่ไม่ส่ง background=true ยังคงรองรับเพื่อความเข้ากันได้

ข้อมูลวัดด้วยจุดแบบเดิมใน `geometry_measurements` ยังเปิดดูย้อนหลังได้ แต่หน้า Review หลักใช้การตรวจอัตโนมัติแล้ว

Docker ติดตั้ง dependencies ของ SAM2 และ mount `.model-cache` แบบอ่านอย่างเดียว ต้องรันขั้นตอนเตรียมโมเดลบน host ก่อนเปิด container; ถ้าตั้ง SAM2_MODEL_DIR เองให้ใช้ path ที่มีอยู่ใน container ด้วย

ทดสอบโดยไม่ส่งภาพไปคลาวด์:

```bash
python -m unittest tests.test_automatic_check -v
python -m unittest tests.test_geometry -v
python -m unittest tests.test_integration -v
python -m unittest tests.test_roboflow -v
python -m tests.postgres_flow
```

อ้างอิง API โมเดลทางการ: https://huggingface.co/docs/transformers/model_doc/sam2


## User management directory

ก่อนเปิด backend รุ่นนี้ ให้รัน `python -m app.migrate` เพื่อเพิ่ม `users.email`, `created_at`, `last_active_at` และ unique email index แบบไม่ลบข้อมูลเดิม หากใช้ SQL Editor ให้รัน migrations 0001–0005 ให้ครบตามลำดับ แล้วรัน `supabase/migrations/202611100006_user_directory.sql` บัญชีเก่าที่ไม่มีวันที่สร้างจะเก็บเป็น NULL และแสดง —; ไม่กำหนดวันที่ย้อนหลังแทนข้อมูลจริง

หน้า Users ใช้ `GET /api/v1/users/page` สำหรับค้นหาชื่อ/username/email กรอง role/status และเรียง/แบ่งหน้า ส่วน `GET /users` เดิมยังคงรูปแบบเดิม Last active บันทึกตอน login และระหว่างใช้งานโดยจำกัดการเขียนหนึ่งครั้งต่อห้านาทีต่อบัญชี การปิดใช้งานยังเก็บประวัติการตรวจและสามารถเปิดบัญชีกลับได้ Admin เท่านั้นที่จัดการบัญชีได้
