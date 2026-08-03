FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libldap2 libsasl2-2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# python-ldap has no wheel: it compiles against libldap. The build headers are
# installed and removed in the same layer so they do not ship in the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libldap-dev libsasl2-dev python3-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y gcc libldap-dev libsasl2-dev python3-dev \
    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY . .
# collectstatic never opens a connection, but settings.py requires a valid
# DATABASE_URL to exist — there is no SQLite fallback. This placeholder is
# parsed and discarded; the real URL arrives at runtime from the environment.
# The key is thrown away with the layer: settings.py refuses the placeholder, and
# baking a fixed one into the image is how a shared secret key gets shipped.
RUN DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
    DATABASE_URL=postgres://build:build@127.0.0.1:5432/build \
    python manage.py collectstatic --noinput

RUN useradd --system --uid 1001 smti && chown -R smti /app
USER smti

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
