# เปิดทั้งระบบด้วย Docker

วาง repositories เป็นโฟลเดอร์ข้างกัน: `ube-backend-starter/` และ `ube-frontend/`
เปิด Docker Desktop ก่อน แล้วใช้ Terminal ใน `ube-backend-starter/`

Backend อ่าน `.env` เดิม (DATABASE_URL ของ Supabase, JWT_SECRET_KEY และ ROBOFLOW_API_KEY)
ไฟล์ .env, รูปอัปโหลด และ weights ถูกกันออกจาก build context ด้วย .dockerignore
Frontend ไม่ต้องใช้ .env.local ใน image; Compose ตั้ง BACKEND_API_URL=http://api:8000/api/v1 ให้ฝั่ง server

หยุดโปรแกรมเดิมที่ใช้พอร์ต 3000/8000 ก่อนสั่ง up

ครั้งแรก:

```bash
docker compose -f compose.fullstack.yaml build
docker compose -f compose.fullstack.yaml run --rm api python -m app.prepare_sam2
docker compose -f compose.fullstack.yaml run --rm api python -m app.migrate
docker compose -f compose.fullstack.yaml up -d
```

ขั้นเตรียม SAM2 ดาวน์โหลดโมเดลทางการและต้องใช้อินเทอร์เน็ต ใช้เวลาครั้งแรก
ฐานข้อมูลอยู่ Supabase เดิม ไม่สร้าง PostgreSQL ในเครื่องเพิ่ม
เปิด http://localhost:3000 และใช้บัญชีเดิม Backend health: http://localhost:8000/health

ครั้งต่อไป:

```bash
docker compose -f compose.fullstack.yaml up -d
docker compose -f compose.fullstack.yaml ps
docker compose -f compose.fullstack.yaml logs -f
```

ปิดระบบ:

```bash
docker compose -f compose.fullstack.yaml down
```

อัปเดตโค้ด:

```bash
docker compose -f compose.fullstack.yaml build
docker compose -f compose.fullstack.yaml run --rm api python -m app.migrate
docker compose -f compose.fullstack.yaml up -d
```

รูปอยู่ใน uploads และโมเดลอยู่ใน .model-cache บนเครื่องผ่าน bind mounts จึงไม่หายเมื่อปิด container
Compose รอ backend healthy ก่อนเปิด frontend ส่วน SAM2 ใช้ CPU ใน container โดย Dockerfile ติดตั้ง PyTorch จาก CPU wheel index ทางการ เพื่อลด dependencies ของ CUDA
หากมีงานวิเคราะห์กำลังทำอยู่ให้รอจนเสร็จก่อนหยุดระบบ
