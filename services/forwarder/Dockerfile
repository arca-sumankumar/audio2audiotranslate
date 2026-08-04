FROM python:3.11-slim
WORKDIR /app
COPY common /app/common
COPY services/forwarder/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/forwarder /app
EXPOSE 50050
CMD ["python", "worker.py"]
