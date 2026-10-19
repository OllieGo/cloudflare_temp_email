#!/usr/bin/env python3
"""
后端自动批量发放临时邮箱（调用已部署 Worker 的建号 API）

依赖: 仅标准库，Python 3.8+

用法示例:
  # 1) 管理员模式：随机子域名批量发 50 个（推荐，后端自动发放主力方式）
  python scripts/issue_address.py \
      --base-url https://mail.ventiecho.com \
      --admin-auth "$ADMIN_PASSWORD" \
      --domain ventiecho.com \
      --count 50 --random-subdomain \
      --out addresses.jsonl

  # 2) 指定子域名（需开启 ENABLE_CREATE_ADDRESS_SUBDOMAIN_MATCH）
  python scripts/issue_address.py --base-url https://mail.ventiecho.com \
      --admin-auth "$ADMIN_PASSWORD" --domain team.ventiecho.com --count 10

  # 3) 匿名公开模式（需开启 ENABLE_USER_CREATE_EMAIL）
  python scripts/issue_address.py --base-url https://mail.ventiecho.com \
      --mode public --domain ventiecho.com --count 5 --random-subdomain

环境变量（可被命令行覆盖）:
  TEMP_MAIL_BASE_URL   Worker 对外地址
  TEMP_MAIL_ADMIN_AUTH 管理后台密码（ADMIN_PASSWORDS 之一）
  TEMP_MAIL_DOMAIN     默认根域名
"""

import argparse
import json
import os
import random
import string
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def random_name(length: int = 10) -> str:
    """符合默认 ADDRESS_REGEX([^a-z0-9]) 的随机名：小写字母 + 数字"""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def post_json(url: str, payload: dict, headers: dict, timeout: int = 30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        if v:
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def build_headers(args):
    if args.mode == "admin":
        auth = args.admin_auth or os.environ.get("TEMP_MAIL_ADMIN_AUTH", "")
        if not auth:
            sys.exit("错误：admin 模式需要 --admin-auth 或环境变量 TEMP_MAIL_ADMIN_AUTH")
        return {"x-admin-auth": auth, "x-custom-auth": args.site_auth}
    return {"x-custom-auth": args.site_auth} if args.site_auth else {}


def issue_one(args, index):
    endpoint = f"{args.base_url.rstrip('/')}/admin/new_address" \
        if args.mode == "admin" else f"{args.base_url.rstrip('/')}/api/new_address"

    name = args.name or random_name(args.name_length)
    if args.unique_suffix and not args.name:
        name = f"{name}{random_name(4)}"

    payload = {
        "name": name,
        "domain": args.domain,
        "enablePrefix": args.enable_prefix,
        "enableRandomSubdomain": args.random_subdomain,
    }
    try:
        status, data = post_json(endpoint, payload, build_headers(args))
        return {
            "ok": status == 200,
            "status": status,
            "address": data.get("address"),
            "jwt": data.get("jwt"),
            "address_id": data.get("address_id"),
            "password": data.get("password"),
            "raw": data,
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode("utf-8", "ignore")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "error": str(e)}


def main():
    p = argparse.ArgumentParser(description="批量发放临时邮箱")
    p.add_argument("--base-url", default=os.environ.get("TEMP_MAIL_BASE_URL", ""),
                   help="Worker 地址，如 https://mail.ventiecho.com")
    p.add_argument("--mode", choices=["admin", "public"], default="admin")
    p.add_argument("--admin-auth", default=os.environ.get("TEMP_MAIL_ADMIN_AUTH", ""))
    p.add_argument("--site-auth", default=os.environ.get("TEMP_MAIL_SITE_AUTH", ""),
                   help="站点私密密码（PASSWORDS），未开启可留空")
    p.add_argument("--domain", default=os.environ.get("TEMP_MAIL_DOMAIN", "ventiecho.com"))
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--name", default="", help="固定邮箱名；留空则随机")
    p.add_argument("--name-length", type=int, default=10)
    p.add_argument("--unique-suffix", action="store_true", help="随机名额外追加 4 位后缀，降低撞库概率")
    p.add_argument("--enable-prefix", action="store_true", help="使用 PREFIX 前缀（默认 tmp）")
    p.add_argument("--random-subdomain", dest="random_subdomain", action="store_true", default=True,
                   help="随机子域名，如 name@x7k2p9q1.ventiecho.com（默认开启）")
    p.add_argument("--no-random-subdomain", dest="random_subdomain", action="store_false")
    p.add_argument("--workers", type=int, default=8, help="并发数")
    p.add_argument("--out", default="", help="输出文件（.jsonl / .csv）")
    args = p.parse_args()

    if not args.base_url:
        sys.exit("错误：需要 --base-url 或环境变量 TEMP_MAIL_BASE_URL")

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(issue_one, args, i) for i in range(max(1, args.count))]
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            if r["ok"]:
                print(f"✅ {r['address']}")
            else:
                print(f"❌ [{r.get('status')}] {r.get('error') or r.get('raw')}")

    ok = [r for r in results if r["ok"]]
    print(f"\n完成：成功 {len(ok)} / 共 {len(results)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            if args.out.endswith(".csv"):
                fp.write("address,jwt,address_id,password\n")
                for r in ok:
                    fp.write(f"{r['address']},{r['jwt']},{r['address_id']},{r.get('password') or ''}\n")
            else:
                for r in ok:
                    fp.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"已写入 {args.out}")

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
