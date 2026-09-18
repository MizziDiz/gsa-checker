// Pages advanced-mode worker: доступ по паролю (2 роли) + API комментариев/гипотез (KV) + статика.
// Роли: master (правит разделы/гипотезы), public (чтение + комментарии).
const DAY = 86400000;
const enc = new TextEncoder();

function b64urlBytes(bytes){ let s=""; for(const b of bytes) s+=String.fromCharCode(b);
  return btoa(s).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""); }
function b64urlEnc(str){ return btoa(unescape(encodeURIComponent(str)))
  .replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""); }
function b64urlDec(s){ s=s.replace(/-/g,"+").replace(/_/g,"/"); return decodeURIComponent(escape(atob(s))); }

async function hmac(payload, secret){
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), {name:"HMAC",hash:"SHA-256"}, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  return b64urlBytes(new Uint8Array(sig));
}
async function makeToken(role, secret){
  const payload = b64urlEnc(JSON.stringify({role, exp: Date.now()+7*DAY}));
  return payload + "." + await hmac(payload, secret);
}
async function readToken(token, secret){
  if(!token || token.indexOf(".") < 0) return null;
  const [payload, sig] = token.split(".");
  if(sig !== await hmac(payload, secret)) return null;
  try{ const o = JSON.parse(b64urlDec(payload)); return (o.exp > Date.now()) ? o.role : null; }
  catch(e){ return null; }
}
function cookie(req, name){
  const c = req.headers.get("Cookie") || "";
  const m = c.match(new RegExp("(?:^|; )"+name+"=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}
function json(obj, status=200, headers={}){
  return new Response(JSON.stringify(obj), {status, headers:{"content-type":"application/json; charset=utf-8", ...headers}});
}

const LOGIN_HTML = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Вход</title>
<style>:root{color-scheme:light dark}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0c1014;color:#e9eef3;
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.card{background:#141a20;border:1px solid #232c35;border-radius:16px;padding:28px 26px;width:300px;
box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{font-size:17px;margin:0 0 4px}.s{color:#93a1ad;font-size:13px;margin:0 0 18px}
input{width:100%;box-sizing:border-box;background:#0c1014;border:1px solid #232c35;border-radius:10px;
color:#e9eef3;font-size:14px;padding:11px 12px;margin-bottom:10px}
button{width:100%;background:#0e9f6e;color:#fff;border:0;border-radius:10px;padding:11px;font-weight:650;
font-size:14px;cursor:pointer}button:disabled{opacity:.6}
.err{color:#e56a7e;font-size:12.5px;min-height:16px;margin-top:8px;text-align:center}</style></head>
<body><form class="card" id="f"><h1>Доступ к сводке</h1><p class="s">Введите пароль</p>
<input id="p" type="password" placeholder="Пароль" autofocus autocomplete="current-password">
<button id="b" type="submit">Войти</button><div class="err" id="e"></div></form>
<script>const f=document.getElementById("f"),p=document.getElementById("p"),b=document.getElementById("b"),e=document.getElementById("e");
f.addEventListener("submit",async ev=>{ev.preventDefault();b.disabled=true;e.textContent="";
try{const r=await fetch("/login",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({password:p.value})});
if(r.ok){location.href="/";}else{e.textContent="Неверный пароль";b.disabled=false;p.select();}}
catch(x){e.textContent="Ошибка сети";b.disabled=false;}});</script></body></html>`;

async function getList(env, key){ return (await env.DATA.get(key, "json")) || []; }

export default {
  async fetch(request, env){
    const url = new URL(request.url), path = url.pathname, method = request.method;
    const role = await readToken(cookie(request, "sess"), env.SESSION_SECRET);

    if(path === "/login"){
      if(method === "POST"){
        let body = {}; try{ body = await request.json(); }catch(e){}
        let r = null;
        if(body.password && body.password === env.MASTER_PASSWORD) r = "master";
        else if(body.password && body.password === env.PUBLIC_PASSWORD) r = "public";
        if(!r) return json({error:"bad password"}, 401);
        const tok = await makeToken(r, env.SESSION_SECRET);
        return json({role:r}, 200, {"Set-Cookie":`sess=${encodeURIComponent(tok)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${7*86400}`});
      }
      return new Response(LOGIN_HTML, {headers:{"content-type":"text/html; charset=utf-8"}});
    }
    if(path === "/logout"){
      return new Response(null, {status:302, headers:{"Location":"/login", "Set-Cookie":"sess=; Path=/; Max-Age=0"}});
    }
    if(path === "/api/me") return json({role: role || null});

    // control-plane: master-only прокси на оркестратор (шара, за туннелем).
    // Токен оркестратора берётся из env и в браузер НЕ уходит.
    if(path === "/ctl" || path.startsWith("/ctl/")){
      if(role !== "master") return json({error:"master only"}, role ? 403 : 401);
      const sub = path.slice(4) || "/";                 // /ctl/status -> /status
      const init = {method, headers:{Authorization:"Bearer "+env.ORCH_TOKEN}};
      if(method === "POST" || method === "PUT"){
        init.body = await request.text();
        init.headers["Content-Type"] = "application/json";
      }
      try{
        const resp = await fetch(env.ORCH_URL.replace(/\/$/,"") + sub + url.search, init);
        return new Response(await resp.text(), {status:resp.status,
          headers:{"content-type":"application/json; charset=utf-8"}});
      }catch(e){ return json({error:"orchestrator unreachable"}, 502); }
    }

    if(path.startsWith("/api/")){
      if(!role) return json({error:"unauthorized"}, 401);

      if(path === "/api/comments"){
        if(method === "GET") return json(await getList(env, "comments"));
        if(method === "POST"){
          let b = {}; try{ b = await request.json(); }catch(e){}
          if(!b.text) return json({error:"empty"}, 400);
          const list = await getList(env, "comments");
          const c = {id:"c"+Date.now().toString(36)+Math.random().toString(36).slice(2,6),
            anchor:String(b.anchor||"").slice(0,120), quote:String(b.quote||"").slice(0,400),
            text:String(b.text).slice(0,2000), author:String(b.author||"").slice(0,40), role, ts:Date.now()};
          list.push(c); await env.DATA.put("comments", JSON.stringify(list));
          return json(c);
        }
      }
      if(path.startsWith("/api/comments/") && method === "DELETE"){
        if(role !== "master") return json({error:"forbidden"}, 403);
        const id = decodeURIComponent(path.split("/").pop());
        const list = (await getList(env, "comments")).filter(c => c.id !== id);
        await env.DATA.put("comments", JSON.stringify(list));
        return json({ok:true});
      }
      if(path === "/api/hypotheses"){
        if(method === "GET") return json(await getList(env, "hypotheses"));
        if(method === "PUT"){
          if(role !== "master") return json({error:"forbidden"}, 403);
          // Неразобранное тело - это НЕ пустой список. Раньше b оставался [],
            // проходил Array.isArray и затирал хранилище, отвечая ok:true.
            // Резервной копии этих данных нигде нет.
            let b; try{ b = await request.json(); }catch(e){ return json({error:'bad json'}, 400); }
          if(!Array.isArray(b)) return json({error:"array expected"}, 400);
          await env.DATA.put("hypotheses", JSON.stringify(b.slice(0, 500)));
          return json({ok:true, count:b.length});
        }
      }
      // Мониторинг изменений. Только master: вкладка живёт под мастер-паролем,
      // поэтому и чтение, и запись закрыты для роли public.
      if(path === "/api/changes"){
        if(role !== "master") return json({error:"master only"}, 403);
        const list = await getList(env, "changes");
        if(method === "GET"){
          const sec = url.searchParams.get("section");
          const out = sec ? list.filter(c => c.section === sec) : list;
          return json(out);
        }
        if(method === "POST"){
          let b; try{ b = await request.json(); }catch(e){ return json({error:"bad json"}, 400); }
          const items = Array.isArray(b) ? b : [b];
          if(!items.length) return json({error:"empty"}, 400);
          // Дедуп по id: отправка из журнала повторяется при каждом прогоне,
          // и без этого одна и та же правка копилась бы в списке.
          const seen = new Set(list.map(c => c.id));
          let added = 0, dup = 0;
          for(const it of items){
            if(!it || !it.id || !it.title){ return json({error:"id and title required"}, 400); }
            if(seen.has(it.id)){ dup++; continue; }
            seen.add(it.id); added++;
            list.push({
              id: String(it.id).slice(0,80),
              section: String(it.section || "Другое").slice(0,40),
              kind: String(it.kind || "").slice(0,20),
              title: String(it.title).slice(0,200),
              body: String(it.body || "").slice(0,2000),
              detail: String(it.detail || "").slice(0,2000),
              ts: Number(it.ts) || Date.now()
            });
          }
          list.sort((a,b) => b.ts - a.ts);
          await env.DATA.put("changes", JSON.stringify(list.slice(0, 2000)));
          return json({ok:true, added, duplicates:dup, total:Math.min(list.length,2000)});
        }
        if(method === "DELETE"){
          const id = url.searchParams.get("id");
          if(!id) return json({error:"id required"}, 400);
          const left = list.filter(c => c.id !== id);
          if(left.length === list.length) return json({error:"not found"}, 404);
          await env.DATA.put("changes", JSON.stringify(left));
          return json({ok:true, removed:list.length-left.length});
        }
      }
      if(path === "/api/weeks"){
        if(method === "GET") return json(await getList(env, "weeks"));
        if(method === "PUT"){
          if(role !== "master") return json({error:"forbidden"}, 403);
          // Неразобранное тело - это НЕ пустой список. Раньше b оставался [],
            // проходил Array.isArray и затирал хранилище, отвечая ok:true.
            // Резервной копии этих данных нигде нет.
            let b; try{ b = await request.json(); }catch(e){ return json({error:'bad json'}, 400); }
          if(!Array.isArray(b)) return json({error:"array expected"}, 400);
          await env.DATA.put("weeks", JSON.stringify(b.slice(0, 300)));
          return json({ok:true, count:b.length});
        }
      }
      return json({error:"not found"}, 404);
    }

    // control-панель — только master; Pages сам отдаёт control.html по чистому URL /control
    // (НЕ переписываем на /control.html — иначе clean-URL 308 зацикливается).
    if(path === "/changes" || path === "/changes.html"){
      if(role !== "master")
        return new Response(LOGIN_HTML, {headers:{"content-type":"text/html; charset=utf-8"}});
      return env.ASSETS.fetch(request);
    }
    if(path === "/control" || path === "/control.html"){
      if(role !== "master")
        return new Response(LOGIN_HTML, {headers:{"content-type":"text/html; charset=utf-8"}});
      return env.ASSETS.fetch(request);
    }
    // остальные страницы — любой залогиненный (master/public)
    if(!role) return new Response(LOGIN_HTML, {headers:{"content-type":"text/html; charset=utf-8"}});
    return env.ASSETS.fetch(request);
  }
};
