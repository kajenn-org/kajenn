# The kajenn base image: kajenn and kajenn-db on python:3.13-slim.
#
# The build context is dist/ holding the kajenn wheel (see .dockerignore):
# the release job passes the wheel it published, the nightly job a wheel
# built from develop.
#
# An application image starts FROM this one, installs its own package and
# copies its config.py to /app. It declares no start command: every
# container starts with `kajenn serve`.
FROM python:3.13-slim

COPY dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl kajenn-db \
    && pip check \
    && rm -rf /tmp/wheels \
    && useradd --uid 10001 --create-home kajenn

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER kajenn
EXPOSE 8080

ENTRYPOINT ["kajenn", "serve", "--host", "0.0.0.0", "--port", "8080"]
CMD ["/app/config.py"]
