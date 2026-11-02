FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libldap2-dev libsasl2-dev libpq-dev libssl-dev libffi-dev \
    libjpeg62-turbo-dev zlib1g-dev libxml2-dev libxslt1-dev \
    git ca-certificates postgresql-client fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
ARG ODOO_COMMIT=0cdbc41e6e1588ba5755df944ba2407bd4ed888f
RUN git init /opt/odoo && cd /opt/odoo \
    && git remote add origin https://github.com/MosheGabayDev/odoo-20.git \
    && git fetch --depth 1 origin "$ODOO_COMMIT" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$ODOO_COMMIT" \
    && printf '%s\n' "$ODOO_COMMIT" > /opt/odoo/SOURCE_COMMIT \
    && rm -rf /opt/odoo/.git
RUN pip install --no-cache-dir -r /opt/odoo/requirements.txt
RUN groupadd --gid 10001 odoo && useradd --uid 10001 --gid 10001 --home-dir /var/lib/odoo odoo \
    && mkdir -p /var/lib/odoo && chown 10001:10001 /var/lib/odoo
COPY --chmod=0444 runtime.py /usr/local/bin/odoo-runtime.py
USER 10001:10001
WORKDIR /opt/odoo
ENTRYPOINT ["python", "/usr/local/bin/odoo-runtime.py"]
CMD ["serve"]
