FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
COPY webhook ./webhook
COPY internal ./internal
COPY fixtures ./fixtures
COPY config ./config
COPY tests ./tests
COPY scripts ./scripts
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
