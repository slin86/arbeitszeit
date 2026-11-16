FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/slin86/arbeitszeit
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]" \
    && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data
# The default is SQLite in /data (mount a volume there). In Kubernetes AZ_DATABASE_URL points at
# PostgreSQL instead, see deploy/README.md.
ENV AZ_DATABASE_URL=sqlite:////data/arbeitszeit.db \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "arbeitszeit.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
