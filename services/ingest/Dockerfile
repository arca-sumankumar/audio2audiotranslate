FROM python:3.11-slim
WORKDIR /app
COPY common /app/common
COPY services/ingest/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/ingest /app
EXPOSE 50010
CMD ["python", "main.py"]
