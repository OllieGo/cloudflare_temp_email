#!/usr/bin/env python3
"""Cloudflare Email Routing 收尾配置（纯标准库，无需 wrangler / 第三方依赖）

做三件事，任何一步因缺少权限失败都只告警、不中断：

1. 尝试通过 API 启用 Email Routing（需要 Zone -> Email Routing -> Edit 权限；
   没有权限时会提示去控制台点一下）
2. 把 apex 的 MX 记录原样复制到主机名 `*`，让随机子域名邮箱也能收信
3. 把 Catch-all 规则改为「发送到 Worker -> cloudflare-temp-email」

环境变量:
    CLOUDFLARE_API_TOKEN  (必需)
    CLOUDFLARE_ACCOUNT_ID (必需)
    MAIL_DOMAINS          JSON 数组，默认取第一个作为收信域名
    WORKER_NAME           默认 cloudflare-temp-email

用法:
    python scripts/setup_email_routing.py
    python scripts/setup_email_routing.py --dry-run
"""

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
WORKER = os.environ.get("WORKER_NAME", "cloudflare-temp-email").strip()
DRY = "--dry-run" in sys.argv


def warn(msg):
    print("::warning::" + msg)


def notice(msg):
    print("::notice::" + msg)


def cf(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Authorization", "Bearer " + TOKEN)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            body = resp.read().decode("utf-8", "ignore")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except ValueError:
            return {"success": False, "_code": e.code, "_body": body[:300]}


def ok(d):
    return isinstance(d, dict) and d.get("success") is True


def resolve_domain():
    raw = os.environ.get("MAIL_DOMAINS", "").strip()
    if raw:
        try:
            arr = json.loads(raw)
            if isinstance(arr, list) and arr:
                return str(arr[0]).strip().rstrip(".")
        except ValueError:
            pass
    return os.environ.get("MAIL_DOMAIN", "").strip().rstrip(".")


def main():
    if not TOKEN or not ACCOUNT:
        warn("缺少 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID，跳过 Email Routing 配置")
        return 0

    domain = resolve_domain()
    if not domain:
        warn("未配置 MAIL_DOMAINS，跳过 Email Routing 配置")
        return 0

    print(f"目标域名: {domain}")

    z = cf("GET", f"/zones?name={domain}")
    zones = z.get("result") or []
    if not zones:
        warn(f"账号下找不到 zone {domain}，请确认域名已接入 Cloudflare 且 Token 有 Zone 读权限")
        return 0
    zone_id = zones[0]["id"]
    print(f"zone_id: {zone_id}  状态: {zones[0].get('status')}")

    # ---------- 1. 启用 Email Routing ----------
    st = cf("GET", f"/zones/{zone_id}/email/routing")
    if ok(st):
        enabled = (st.get("result") or {}).get("enabled")
        print(f"Email Routing 当前状态: enabled={enabled}")
        if not enabled:
            en = cf("POST", f"/zones/{zone_id}/email/routing/enable", {"enabled": True})
            print("启用结果:", "成功" if ok(en) else "失败(需控制台操作)")
    else:
        warn("无法读取 Email Routing 状态(403)，Token 缺少 Zone->Email Routing 权限；"
             "请在控制台 Email Routing 页面点「启用」，本脚本的后续步骤仍会尝试执行")

    # ---------- 2. 通配 MX ----------
    dns = cf("GET", f"/zones/{zone_id}/dns_records?per_page=100&type=MX")
    if not ok(dns):
        warn("读取 DNS 记录失败，跳过通配 MX 配置")
        mx_records = []
    else:
        mx_records = [r for r in dns["result"] if r.get("type") == "MX"]

    apex_mx = [r for r in mx_records if r.get("name", "").rstrip(".") == domain]
    if not apex_mx:
        warn(f"{domain} 上还没有 MX 记录，说明 Email Routing 尚未启用；"
             f"启用后重跑本脚本即可自动补齐通配 MX")
    else:
        for r in apex_mx:
            target = f"*.{domain}"
            exists = [x for x in mx_records
                      if x.get("name", "").rstrip(".") == target
                      and x.get("content") == r["content"]]
            if exists:
                print(f"通配 MX 已存在: {target} -> {r['content']} (priority {r.get('priority')})")
                continue
            body = {
                "type": "MX",
                "name": target,
                "content": r["content"],
                "priority": r.get("priority", 0),
                "ttl": 1,
                "proxied": False,
            }
            if DRY:
                print(f"[dry-run] 将创建 {target} -> {r['content']} priority={body['priority']}")
                continue
            res = cf("POST", f"/zones/{zone_id}/dns_records", body)
            if ok(res):
                print(f"✅ 已创建通配 MX: {target} -> {r['content']} (priority {body['priority']})")
            else:
                warn(f"创建通配 MX 失败: {json.dumps(res, ensure_ascii=False)[:300]}")

    # ---------- 3. Catch-all -> Worker ----------
    rules = cf("GET", f"/zones/{zone_id}/email/routing/rules")
    if not ok(rules):
        warn("读取邮件路由规则失败，无法设置 Catch-all")
        return 0

    catch_all = None
    for r in rules.get("result", []):
        if any(m.get("type") == "all" for m in r.get("matchers", [])):
            catch_all = r
            break
    if not catch_all:
        warn("未找到 Catch-all 规则，请在控制台手动添加")
        return 0

    already = any(a.get("type") == "worker" and WORKER in (a.get("value") or [])
                  for a in catch_all.get("actions", []))
    if already and catch_all.get("enabled"):
        print(f"✅ Catch-all 已指向 Worker {WORKER}")
        return 0

    body = {
        "name": catch_all.get("name") or "catch-all",
        "matchers": [{"type": "all"}],
        "actions": [{"type": "worker", "value": [WORKER]}],
        "enabled": True,
        "priority": catch_all.get("priority", 2147483647),
    }
    if DRY:
        print(f"[dry-run] 将把 Catch-all 规则 {catch_all['id']} 改为 worker:{WORKER}")
        return 0

    res = cf("PUT", f"/zones/{zone_id}/email/routing/rules/{catch_all['id']}", body)
    if ok(res):
        print(f"✅ Catch-all 已改为「发送到 Worker -> {WORKER}」")
        notice("收信链路已就绪：外部邮件 -> 通配 MX -> Email Routing -> Catch-all -> Worker")
    else:
        warn("设置 Catch-all 失败: " + json.dumps(res, ensure_ascii=False)[:400]
             + "  —— 常见原因是 Email Routing 还没启用，启用后重跑即可")
    return 0


if __name__ == "__main__":
    sys.exit(main())
