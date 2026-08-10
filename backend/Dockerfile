FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY frontend/static-demo.html /app/frontend/static-demo.html
COPY sample-documents /app/sample-documents
COPY data /app/data
RUN python /app/backend/scripts/seed_sample_documents.py && python /app/backend/scripts/ingest.py
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

