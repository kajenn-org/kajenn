FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .
COPY examples/remote_openapi/__init__.py examples/remote_openapi/app.py examples/remote_openapi/healthcheck.py ./examples/remote_openapi/
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=5s --timeout=4s --start-period=10s --retries=6 \
    CMD ["python", "-m", "examples.remote_openapi.healthcheck"]
ENTRYPOINT ["python", "-m", "kajenn.remote_runner"]
CMD ["--factory", "examples.remote_openapi.app:create_application", "--address", "tcp:0.0.0.0:8765", "--allow-network-listener", "--mount", "demo"]
