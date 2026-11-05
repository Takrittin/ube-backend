FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements-sam2.txt ./
RUN pip install --no-cache-dir "torch>=2.5.1,<3" "torchvision>=0.20.1,<1" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-sam2.txt
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY test-ui ./test-ui
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
