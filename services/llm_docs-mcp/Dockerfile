FROM python:3.9-slim

# Configurar zona horaria
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install prometheus_client
RUN python -m nltk.downloader punkt || true

COPY documents/ ./documents/
COPY llm-models/ ./llm-models/
COPY gateway.py .

EXPOSE 8000

CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]