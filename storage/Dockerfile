
FROM python:3.13-slim

WORKDIR /app

# Prevents interactive prompts when installing packages
ENV DEBIAN_FRONTEND=noninteractive

COPY pyproject.toml ./
RUN pip install uv && \
    uv pip install --no-cache --system .

COPY src/ ./src
