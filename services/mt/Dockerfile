FROM python:3.11-slim
WORKDIR /app
COPY common /app/common
COPY services/mt/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/mt /app
EXPOSE 50030
CMD ["python", "worker.py"]
