FROM python:3.11-slim
WORKDIR /app
COPY common /app/common
COPY services/asr/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/asr /app
EXPOSE 50020
CMD ["python", "worker.py"]
