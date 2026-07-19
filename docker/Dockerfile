# Stage 1: bundle Simple Icons (simpleicons.org, CC0 set) INTO the image at build
# time. The app itself makes no external calls, ever - brand SVGs are served from
# the container. Pinned version; the map file carries slug -> brand hex + aliases.
FROM python:3.12-slim AS icons
RUN python - <<'PYICONS'
import io, json, os, re, tarfile, unicodedata, urllib.request

VER = "16.27.0"
url = f"https://registry.npmjs.org/simple-icons/-/simple-icons-{VER}.tgz"
buf = io.BytesIO(urllib.request.urlopen(url, timeout=180).read())
os.makedirs("/si/simple-icons", exist_ok=True)
data = None
with tarfile.open(fileobj=buf, mode="r:gz") as t:
    for m in t.getmembers():
        if m.name.startswith("package/icons/") and m.name.endswith(".svg"):
            with t.extractfile(m) as f:
                with open(os.path.join("/si/simple-icons", os.path.basename(m.name)), "wb") as o:
                    o.write(f.read())
        elif m.name.endswith("data/simple-icons.json"):
            data = json.load(t.extractfile(m))
items = data["icons"] if isinstance(data, dict) else data

def norm(s):
    s = s.lower().replace("+", "plus").replace(".", "dot").replace("&", "and")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s)

colors, alias = {}, {}
for it in items:
    slug = it.get("slug") or norm(it["title"])
    colors[slug] = it.get("hex", "888888")
    n = norm(it["title"])
    if n != slug:
        alias[n] = slug
    for aka in (it.get("aliases") or {}).get("aka", []):
        na = norm(aka)
        if na and na != slug and na not in colors:
            alias[na] = slug
svgs = len([f for f in os.listdir("/si/simple-icons") if f.endswith(".svg")])
assert svgs > 2000 and len(colors) > 2000, f"suspicious icon count: {svgs} svgs / {len(colors)} entries"
for p in ("okta", "auth0", "authentik"):
    assert os.path.exists(f"/si/simple-icons/{p}.svg"), f"missing provider icon {p}"
with open("/si/simple-icons/si-map.json", "w") as f:
    json.dump({"v": VER, "colors": colors, "alias": alias}, f, separators=(",", ":"))
print(f"bundled {svgs} icons, {len(colors)} colors, {len(alias)} aliases")
PYICONS

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 idpvault \
    && command -v setpriv   # entrypoint drops root -> idpvault with it; fail the build if absent
WORKDIR /srv/idpvault

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./frontend
COPY --from=icons /si/simple-icons ./frontend/vendor/simple-icons
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# No USER directive: the entrypoint starts as root (to self-seed the master key
# and fix volume ownership on first boot), then drops to idpvault (uid 10001).
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import httpx; httpx.get('http://localhost:8080/healthz').raise_for_status()"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
