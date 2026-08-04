FROM python:3.11-slim
WORKDIR /app
COPY services/demo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/demo /app
EXPOSE 50060
CMD ["python", "app.py"]
