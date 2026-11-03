ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL io.odoo.release.public-proxy="1"
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
RUN mkdir -p /opt/odoo/custom-addons
RUN python - <<'PY'
import json, subprocess, pathlib, shutil
pin=json.loads(pathlib.Path('/opt/odoo/deploy/test/vendor-addons.json').read_text())
assert pin['repository']=='https://github.com/muk-it/odoo-modules.git'
assert pin['commit']=='b9917747e54cab6eed55527e7ee309f8ee71b6e9'
assert pin['modules']==['muk_mcp','muk_mcp_access','muk_web_utils']
root=pathlib.Path('/tmp/odoo-reviewed-vendor')
subprocess.run(['git','init',str(root)],check=True)
subprocess.run(['git','-C',str(root),'fetch','--depth','1',pin['repository'],pin['commit']],check=True)
subprocess.run(['git','-C',str(root),'checkout','--detach','FETCH_HEAD'],check=True)
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()==pin['commit']
for name in pin['modules']:shutil.copytree(root/name,pathlib.Path('/opt/odoo/custom-addons')/name)
shutil.copy2(root/'LICENSE','/opt/odoo/custom-addons/MUK-LICENSE')
pathlib.Path('/opt/odoo/VENDOR_ADDONS.json').write_text(json.dumps(pin,sort_keys=True))
shutil.rmtree(root)
PY
RUN pip install --no-cache-dir -r /opt/odoo/requirements.txt
RUN groupadd --gid 10001 odoo && useradd --uid 10001 --gid 10001 --home-dir /var/lib/odoo odoo \
    && mkdir -p /var/lib/odoo && chown 10001:10001 /var/lib/odoo
COPY --chmod=0444 runtime.py /usr/local/bin/odoo-runtime.py
COPY --chmod=0444 smoke.py /usr/local/bin/odoo-smoke.py
USER 10001:10001
WORKDIR /opt/odoo
ENTRYPOINT ["python", "/usr/local/bin/odoo-runtime.py"]
CMD ["serve"]
