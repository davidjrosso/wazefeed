FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY app.py core.py operations.py catalog.py direct.py agenda.py accounts.py monitoring.py collect_waze.py wsgi.py manage.py healthcheck.py ./
COPY config ./config
COPY static ./static
COPY templates ./templates
RUN mkdir /data && chown app:app /data
USER app
CMD ["gunicorn","--bind","unix:/run/waze/app.sock","--umask","007","--workers","2","--timeout","30","--worker-tmp-dir","/tmp","--access-logfile","-","--error-logfile","-","--forwarder-headers","","wsgi:application"]
