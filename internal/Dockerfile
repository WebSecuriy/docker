FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
RUN mkdir /data && chown 10001:10001 /data
COPY server.py ./
USER 10001:10001
CMD ["python", "-u", "server.py"]
