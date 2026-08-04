FROM python:3.11-slim
WORKDIR /app
COPY common /app/common
COPY services/tts/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/tts /app
EXPOSE 50040
CMD ["python", "worker.py"]
