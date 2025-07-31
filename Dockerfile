FROM python:3.11-slim

WORKDIR /app
COPY . .

# Install dependencies and set safe ENV
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONUNBUFFERED=1

CMD ["python", "progress.py"]
