FROM python:3.11-slim
WORKDIR /app
COPY common /app/common
COPY services/gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/gateway /app
EXPOSE 51000
CMD ["python", "main.py"]
