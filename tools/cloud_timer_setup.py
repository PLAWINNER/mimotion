"""Local-only setup page for cron-job.org. No credentials are written to disk."""
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

REPO = "PLAWINNER/mimotion"
WORKFLOW = f"/repos/{REPO}/actions/workflows/run.yml"
DISPATCH_URL = "https://api.github.com" + WORKFLOW + "/dispatches"
BODY = {"ref": "master", "inputs": {"scheduled": True}}
TIMES = ((9, 0), (11, 34), (14, 8), (16, 42), (19, 16), (21, 50))
STATE_FILE = Path(__file__).with_suffix(".state.json")
STATE = {"status": "ready", "message": "等待填写专用令牌", "jobs": []}
LOCK = threading.Lock()
SETUP_KEY = secrets.token_urlsafe(32)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(service, method, path, token, data=None):
    roots = {"github": "https://api.github.com", "cron": "https://api.cron-job.org"}
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
               "Accept": "application/json", "User-Agent": "mimotion-cloud-timer-setup"}
    request = Request(roots[service] + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    try:
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as error:
        # 服务端响应可能含认证信息，只保留服务名称和状态码。
        raise RuntimeError(f"{service} 返回 HTTP {error.code}，请检查令牌权限、有效期和 API 配额。") from None
    except (URLError, TimeoutError):
        raise RuntimeError(f"连接 {service} 超时或网络异常。重新提交会复用已创建的任务。") from None


def job_specs(github_token):
    entries = [(f"{h:02}:{m:02}", [h], [m]) for h, m in TIMES]
    entries += [("catchup-day", list(range(9, 22)), [7, 27, 47]), ("catchup-evening", [22], [7, 27])]
    return [{
        "title": f"mimotion / {REPO} / {label}", "url": DISPATCH_URL,
        "enabled": True, "saveResponses": False, "requestMethod": 1,
        "schedule": {"timezone": "Asia/Shanghai", "expiresAt": 0, "hours": hours,
                     "minutes": minutes, "mdays": [-1], "months": [-1], "wdays": [-1]},
        "extendedData": {"headers": {"Authorization": "Bearer " + github_token,
                         "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                         "User-Agent": "mimotion-cloud-timer"}, "body": json.dumps(BODY)},
    } for label, hours, minutes in entries]


def update_state(status, message, jobs):
    global STATE
    STATE = {"status": status, "message": message, "jobs": jobs}
    STATE_FILE.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")


def configure(github_token, cron_token):
    completed = []
    try:
        update_state("working", "验证两个服务的令牌权限……", completed)
        workflow = api("github", "GET", WORKFLOW, github_token)
        if workflow.get("state") != "active":
            raise RuntimeError("GitHub 工作流未启用，请先启用刷步数工作流。")
        inventory = api("cron", "GET", "/jobs", cron_token)
        if inventory.get("someFailed"):
            raise RuntimeError("cron-job.org 任务列表不完整，暂不创建，以免重复。")
        existing = inventory.get("jobs", [])
        # scheduled=true 同时验证调用权限，当前时段已成功时会跳过。
        api("github", "POST", WORKFLOW + "/dispatches", github_token, BODY)
        for index, job in enumerate(job_specs(github_token)):
            if index:
                time.sleep(13)  # cron-job.org 创建限额：每分钟最多 5 次。
            update_state("working", f"配置任务 {index + 1}/8（约需两分钟）……", completed)
            matches = [old for old in existing if old.get("title") == job["title"]]
            if len(matches) > 1 or any(old.get("url") != DISPATCH_URL for old in matches):
                raise RuntimeError("发现同名任务冲突，请先在 cron-job.org 核对；未覆盖该任务。")
            if matches:
                job_id = matches[0]["jobId"]
                api("cron", "PATCH", f"/jobs/{job_id}", cron_token, {"job": job})
            else:
                job_id = api("cron", "PUT", "/jobs", cron_token, {"job": job})["jobId"]
            saved = api("cron", "GET", f"/jobs/{job_id}", cron_token)["jobDetails"]
            if any(saved.get(field) != job[field] for field in ("enabled", "url", "schedule", "requestMethod")):
                raise RuntimeError("云端保存的配置与计划不符，请检查任务设置。")
            saved_headers = {key.lower(): value for key, value in saved.get("extendedData", {}).get("headers", {}).items()}
            if saved_headers.get("authorization") != "Bearer " + github_token:
                raise RuntimeError("云端认证请求头保存验证失败。")
            if json.loads(saved.get("extendedData", {}).get("body", "{}")) != BODY:
                raise RuntimeError("云端请求正文保存验证失败。")
            completed.append({"id": job_id, "title": job["title"], "enabled": saved["enabled"],
                              "nextExecution": saved.get("nextExecution")})
        update_state("configured", "8 个云端任务已保存并启用。首次定时触发结果仍需在 GitHub Actions 核对。", completed)
    except Exception as error:
        message = str(error) if type(error) is RuntimeError else f"配置未完成：{type(error).__name__}。可重新提交，已创建的任务会复用。"
        update_state("error", message, completed)
    finally:
        LOCK.release()


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>配置 mimotion 云定时</title>
<style>body{font:16px/1.65 system-ui,sans-serif;background:#f5f7fa;color:#152338;margin:40px auto;padding:0 24px;max-width:760px}main{background:white;padding:28px;border-radius:16px}h1{font-size:27px}label{display:block;font-weight:600;margin-top:22px}input{box-sizing:border-box;width:100%;padding:12px;border:1px solid #9cabbc;border-radius:7px;font-size:16px}button{padding:12px 22px;margin-top:24px;border:0;border-radius:8px;background:#176346;color:white;font-size:16px;cursor:pointer}button:disabled{opacity:.55}a{color:#185db3}small{color:#4d5c70}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3f8;padding:16px;border-radius:8px}</style>
<main><h1>配置 mimotion 云定时</h1>
<p>每天北京时间 <b>09:00、11:34、14:08、16:42、19:16、21:50</b> 刷新；另建两个补跑检查任务。已完成的账号会自动跳过。</p>
<p>本页面仅在这台电脑上运行。GitHub 专用令牌将保存到 cron-job.org，用于触发本仓库；cron-job.org API key 仅用于配置其任务。两个密钥均不写入本地文件，Zepp 账号密码继续留在 GitHub Secrets。</p>
<form id="setup"><label for="github">1. GitHub Fine-grained token</label>
<small><a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener noreferrer">创建专用令牌</a>：Resource owner 选 PLAWINNER；Only select repositories 仅选 mimotion；Repository permissions → Actions 选 Read and write。到期后需更新。</small>
<input id="github" type="password" autocomplete="new-password" required placeholder="github_pat_…">
<label for="cron">2. cron-job.org API key</label>
<small><a href="https://console.cron-job.org/" target="_blank" rel="noopener noreferrer">注册或登录 cron-job.org</a>，在 Settings 中创建 API key。</small>
<input id="cron" type="password" autocomplete="new-password" required>
<button id="submit" type="submit">验证令牌并启用云定时</button></form>
<p><small>配置约需两分钟。完成后电脑可以关机；服务发出触发请求后，GitHub 执行仍可能排队。</small></p>
<pre id="status" role="status" aria-live="polite">等待填写专用令牌</pre>
<p><a href="https://github.com/PLAWINNER/mimotion/actions/workflows/run.yml" target="_blank" rel="noopener noreferrer">查看 GitHub 运行结果</a></p></main>
<script>
const key="__SETUP_KEY__", form=document.getElementById('setup'), output=document.getElementById('status'), button=document.getElementById('submit');
async function refresh(){const r=await fetch('/status');const s=await r.json();output.textContent=s.message+'\\n'+s.jobs.map(j=>j.title+'：已启用').join('\\n');button.disabled=s.status==='working';}
form.addEventListener('submit',async e=>{e.preventDefault();button.disabled=true;const data={github:document.getElementById('github').value.trim(),cron:document.getElementById('cron').value.trim()};form.reset();try{const r=await fetch('/configure',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Key':key},body:JSON.stringify(data)});const s=await r.json();if(!r.ok){output.textContent=s.message;button.disabled=false;}else await refresh();}catch{output.textContent='本地配置助手连接中断，请重新启动。';button.disabled=false;}finally{data.github='';data.cron='';}});
setInterval(()=>refresh().catch(()=>{}),3000);refresh();
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value, html=False):
        content = value.encode("utf-8") if html else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.valid_host():
            self.respond(403, {"message": "Invalid host"})
        elif self.path == "/":
            self.respond(200, PAGE.replace("__SETUP_KEY__", SETUP_KEY), html=True)
        elif self.path == "/status":
            self.respond(200, STATE)
        else:
            self.respond(404, {"message": "Not found"})

    def do_POST(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if not self.valid_host() or self.headers.get("Origin") != origin or self.headers.get("X-Setup-Key") != SETUP_KEY:
            self.respond(403, {"message": "请从本地配置页面提交。"})
            return
        if self.path != "/configure" or self.headers.get("Content-Type") != "application/json":
            self.respond(400, {"message": "请求格式不正确。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            github, cron = data["github"], data["cron"]
            if not all(isinstance(key, str) and 20 < len(key) < 2048 and key.isascii() and not any(c.isspace() for c in key) for key in (github, cron)):
                raise ValueError()
            if not github.startswith("github_pat_"):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.respond(400, {"message": "请输入 GitHub Fine-grained token 和 cron-job.org API key。"})
            return
        if not LOCK.acquire(blocking=False):
            self.respond(409, {"message": "配置正在进行，请等待完成。"})
            return
        update_state("working", "开始验证并创建云端任务……", [])
        threading.Thread(target=configure, args=(github, cron), daemon=True).start()
        self.respond(202, {"message": "已开始配置。"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"http://127.0.0.1:{server.server_port}/", flush=True)
    server.serve_forever()
