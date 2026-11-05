FROM python:3.12-slim
WORKDIR /srv
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY api/openapi.yaml api/openapi.yaml
COPY server/app server/app
WORKDIR /srv/server
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
