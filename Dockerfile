FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"
ENV AZ_DATABASE_URL=sqlite:////data/arbeitszeit.db
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "arbeitszeit.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
