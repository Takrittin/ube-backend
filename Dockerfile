FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-sam2.txt ./
RUN pip install --no-cache-dir -r requirements-sam2.txt
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY test-ui ./test-ui
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
