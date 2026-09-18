#!/usr/bin/env python3
"""Деплой каталога site/ на Cloudflare Pages (direct upload, без wrangler/node).

Статика (index.html, hypotheses.html) идёт через хеш→check-missing→upload→manifest;
_worker.js (advanced mode) отправляется отдельным полем в deployment. Токен читается из
/root/.cloudflare/token, НЕ печатается.

  python3 deploy_pages.py [--project region-report] [--dir site]
"""
import argparse, base64, json, mimetypes, os, sys
import blake3, requests

ACCT = "57b5616edd7e1a2f38b99d00244fab51"
TOKEN = open("/root/.cloudflare/token").read().strip()
API = "https://api.cloudflare.com/client/v4"
HDR = {"Authorization": f"Bearer {TOKEN}"}
# спец-файлы Pages, которые НЕ являются статикой в манифесте
SPECIAL = {"_worker.js", "_routes.json", "_headers", "_redirects"}


def file_hash(path: str) -> str:
    data = open(path, "rb").read()
    b64 = base64.b64encode(data).decode()
    ext = os.path.splitext(path)[1].lstrip(".")
    return blake3.blake3((b64 + ext).encode()).hexdigest()[:32]


def ctype(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="region-report")
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(__file__), "site"))
    args = ap.parse_args()
    proj, d = args.project, args.dir

    files = sorted(os.listdir(d))
    assets = [f for f in files if f not in SPECIAL and os.path.isfile(os.path.join(d, f))]
    worker = os.path.join(d, "_worker.js") if "_worker.js" in files else None
    print(f"assets: {assets}  worker: {'да' if worker else 'нет'}")

    # 1) хеши статики
    manifest, blobs = {}, {}
    for f in assets:
        p = os.path.join(d, f)
        h = file_hash(p)
        manifest["/" + f] = h
        blobs[h] = (base64.b64encode(open(p, "rb").read()).decode(), ctype(p))
    print("manifest:", manifest)

    # 2) upload-token (JWT для assets-эндпоинтов)
    r = requests.get(f"{API}/accounts/{ACCT}/pages/projects/{proj}/upload-token", headers=HDR, timeout=30)
    jd = r.json()
    if not jd.get("success"):
        print("upload-token FAIL:", jd.get("errors")); sys.exit(1)
    jwt = jd["result"]["jwt"]
    JHDR = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

    # 3) check-missing
    r = requests.post("https://api.cloudflare.com/client/v4/pages/assets/check-missing",
                      headers=JHDR, data=json.dumps({"hashes": list(blobs)}), timeout=30)
    cm = r.json()
    if not cm.get("success"):
        print("check-missing FAIL:", cm.get("errors")); sys.exit(1)
    missing = cm["result"]
    print(f"missing (нужно залить): {len(missing)} из {len(blobs)}")

    # 4) upload недостающих
    if missing:
        payload = [{"key": h, "value": blobs[h][0], "metadata": {"contentType": blobs[h][1]},
                    "base64": True} for h in missing]
        r = requests.post("https://api.cloudflare.com/client/v4/pages/assets/upload",
                          headers=JHDR, data=json.dumps(payload), timeout=120)
        up = r.json()
        if not up.get("success"):
            print("upload FAIL:", up.get("errors")); sys.exit(1)
        print("upload OK")

    # 5) создать deployment (multipart: manifest + _worker.js)
    data = {"manifest": json.dumps(manifest)}
    fileparts = {}
    if worker:
        fileparts["_worker.js"] = ("_worker.js", open(worker, "rb").read(), "application/javascript")
    r = requests.post(f"{API}/accounts/{ACCT}/pages/projects/{proj}/deployments",
                      headers=HDR, data=data, files=fileparts or None, timeout=120)
    dep = r.json()
    if not dep.get("success"):
        print("deployment FAIL:", json.dumps(dep.get("errors"), ensure_ascii=False)); sys.exit(1)
    res = dep["result"]
    # success у API означает «запрос принят», а не «сборка прошла». Раньше
    # печаталось DEPLOY OK и код выхода 0 даже при провалившейся стадии сборки.
    _stage = (res.get("latest_stage") or {})
    _st = str(_stage.get("status") or "")
    if _st in ("failure", "canceled"):
        print(f"DEPLOY FAILED: стадия {_stage.get('name')} → {_st}", file=sys.stderr)
        sys.exit(1)
    print("DEPLOY OK" if _st == "success" else
          f"DEPLOY ПРИНЯТ, НЕ ПОДТВЕРЖДЁН (стадия {_stage.get('name')} → {_st or 'н/д'})")
    print("  url:", res.get("url"))
    print("  id :", res.get("id"))
    stage = (res.get("latest_stage") or {})
    print("  stage:", stage.get("name"), stage.get("status"))


if __name__ == "__main__":
    main()
