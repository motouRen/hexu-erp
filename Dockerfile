FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock.txt requirements-production.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt -r requirements-production.txt \
    && useradd --create-home erp
COPY --chown=erp:erp . .
RUN chown erp:erp /app
USER erp
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-"]
