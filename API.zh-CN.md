# 自动生成邮箱接口文档

> 适用部署：Worker `cloudflare-temp-email`，对外域名 `https://mail.ventiecho.com`
> 分支：`cloudflare`（部署触发分支）｜源码：`worker/src/mails_api/new_address.ts`、`worker/src/admin_api/address_api.ts`、`worker/src/common.ts`

---

## 0. 一分钟结论

自动生成邮箱**有两个接口**，取决于你要不要用管理员权限：

| 接口 | 鉴权 | 用途 | 前缀 |
| --- | --- | --- | --- |
| `POST /api/new_address` | **无需鉴权**（可匿名调用） | 对外自动发号、脚本批量发放、页面「随机生成」按钮 | 会带 `PREFIX`（当前 `tmp`） |
| `POST /admin/new_address` | 请求头 `x-admin-auth: <管理密码>` | 后台手工建号、你自己的发放服务 | 由 `enablePrefix` 控制，不传则不加 |

**对外提供自动发号服务，用第一个。** 它不需要登录，一次请求返回一个完整可用的邮箱地址 + 访问 JWT。

---

## 1. `POST /api/new_address` —— 匿名自动发号（推荐）

### 请求

```
POST https://mail.ventiecho.com/api/new_address
Content-Type: application/json
```

**请求体（所有字段都可选，传 `{}` 也行）**

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 随机生成 | 邮箱用户名部分。不传或命中 `DISABLE_CUSTOM_ADDRESS_NAME` 时由服务端随机生成。会按正则剔除特殊字符并 trim |
| `domain` | string | 从 `DOMAINS` 随机挑一个 | 收信域名。必须是 `DOMAINS` 里的域名，或其子域名（需开启子域名后缀匹配） |
| `enableRandomSubdomain` | boolean | `false` | **核心开关**：`true` 时在域名前面拼一段随机子域名，实现「无限邮箱」 |
| `cf_token` | string | — | Cloudflare Turnstile 验证码 token。**只有配置了 Turnstile 时才必填**，当前部署未开启，可忽略 |

> 用户名前缀：匿名调用会强制加上 `PREFIX` 环境变量（当前为 `tmp`），即 `name="abc"` 实际生成 `tmpabc@...`。
> 长度限制：`MIN_ADDRESS_LEN`（默认 1）~ `MAX_ADDRESS_LEN`（默认 30），作用于 `name` 本身。

### 响应 `200`

```json
{
  "address": "tmpa1b2c3d4@7f3k9m2q.ventiecho.com",
  "jwt": "eyJhbGciOiJIUzI1NiIs...",
  "password": null,
  "address_id": 42
}
```

| 字段 | 说明 |
| --- | --- |
| `address` | 完整邮箱地址。**这个就是能收信的地址**，直接展示给用户 |
| `jwt` | 访问该邮箱的凭证（HS256，payload 为 `{address, address_id}`）。后续收信接口用 `Authorization: Bearer <jwt>` |
| `password` | 邮箱独立密码。仅当 `ENABLE_ADDRESS_PASSWORD=true` 时才有值，当前为 `null` |
| `address_id` | 数据库自增 ID |

### 示例

**随机子域名（无限邮箱模式）**

```bash
curl -X POST https://mail.ventiecho.com/api/new_address \
  -H "Content-Type: application/json" \
  -d '{"enableRandomSubdomain": true}'
```

**指定用户名 + 随机子域名**

```bash
curl -X POST https://mail.ventiecho.com/api/new_address \
  -H "Content-Type: application/json" \
  -d '{"name": "signup", "enableRandomSubdomain": true}'
```

**指定固定子域名**（需子域名后缀匹配已开启）

```bash
curl -X POST https://mail.ventiecho.com/api/new_address \
  -H "Content-Type: application/json" \
  -d '{"name": "signup", "domain": "demo.ventiecho.com"}'
```

**Python**

```python
import requests

BASE = "https://mail.ventiecho.com"

def issue_address(name=None, random_subdomain=True):
    r = requests.post(
        f"{BASE}/api/new_address",
        json={"name": name, "enableRandomSubdomain": random_subdomain},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()          # {"address":..., "jwt":..., "password":..., "address_id":...}

addr = issue_address()
print(addr["address"], addr["jwt"])
```

### 错误码

| 状态码 | 触发条件 |
| --- | --- |
| `400` | 用户名命中黑名单、长度超限、域名非法、地址已存在、Turnstile 校验失败 |
| `403` | `ENABLE_USER_CREATE_EMAIL=false`（功能关闭）；或 `DISABLE_ANONYMOUS_USER_CREATE_EMAIL=true` 且未登录 |
| `400` | 登录用户且达到角色地址数量上限 |

错误响应是**纯文本**而不是 JSON，例如 `Name[xxx]is blocked`、`创建地址失败: ...`。

---

## 2. `POST /admin/new_address` —— 管理端发号

### 请求

```
POST https://mail.ventiecho.com/admin/new_address
Content-Type: application/json
x-admin-auth: <ADMIN_PASSWORDS 之一>
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | **是** | 用户名，缺失直接 400 |
| `domain` | string | 否 | 不传则从 `DOMAINS` 随机选 |
| `enablePrefix` | boolean | 否 | 是否加 `PREFIX` 前缀。**管理端默认不加** |
| `enableRandomSubdomain` | boolean | 否 | 同上，生成随机子域名 |

管理端**跳过**长度配置校验、域名白名单校验和用户名正则校验（`checkLengthByConfig=false`、`checkAllowDomains=false`、`enableCheckNameRegex=false`），所以能建出不合规的名字——别乱填。

### 响应

与 `/api/new_address` 完全相同的四字段 JSON。

### 示例

```bash
curl -X POST https://mail.ventiecho.com/admin/new_address \
  -H "Content-Type: application/json" \
  -H "x-admin-auth: 你的管理密码" \
  -d '{"name": "ops", "enablePrefix": false, "enableRandomSubdomain": true}'
```

```json
{ "address": "ops@4w9maht0.ventiecho.com", "jwt": "...", "password": null, "address_id": 43 }
```

未带或密码错误 → `401`。

---

## 3. 子域名（无限邮箱）机制

这是本次部署特意打开的能力，三个开关都在 `scripts/gen-wrangler.mjs` 里：

| 变量 | 当前值 | 作用 |
| --- | --- | --- |
| `DOMAINS` | `["ventiecho.com"]` | 允许收信的根域名 |
| `RANDOM_SUBDOMAIN_DOMAINS` | `["ventiecho.com"]` | 允许生成随机子域名的域名白名单 |
| `RANDOM_SUBDOMAIN_LENGTH` | `8` | 随机子域名长度（1~63，字符集 `a-z0-9`） |
| `ENABLE_CREATE_ADDRESS_SUBDOMAIN_MATCH` | `true` | 允许手动指定 `xxx.ventiecho.com` 作为 domain |
| `ENABLE_USER_CREATE_EMAIL` | `true` | 开放匿名发号 |

生成逻辑（`worker/src/common.ts`）：

```
addressDomain = enableRandomSubdomain ? `${random(8)}.${domain}` : domain
address       = `${PREFIX}${name}@${addressDomain}`
```

随机子域名撞库时会**自动重试**（最多 `MAX_RANDOM_SUBDOMAIN_ATTEMPTS` 次），全部失败才返回「地址已存在」。

### ⚠️ 收信的前提

子域名能收信靠的是 DNS 里的**通配 MX**：

```
*.ventiecho.com  →  route1.mx.cloudflare.net / route2 / route3
```

已由 `scripts/setup_email_routing.py` 配置好（把 apex 的三条 MX 原样复制到主机名 `*`）。Catch-all 规则动作为 `worker → cloudflare-temp-email`。
**Cloudflare 的子域名不会继承主域的 Email Routing 配置**，没有通配 MX 的话地址能生成但永远收不到信。

---

## 4. 拿到邮箱后怎么用

发号返回的 `jwt` 就是该邮箱的访问凭证。后续所有 `/api/*` 接口都需要它：

```bash
curl https://mail.ventiecho.com/api/mails \
  -H "Authorization: Bearer <发号返回的 jwt>"
```

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/mails` | GET | 收件列表 |
| `/api/mail/:mail_id` | GET | 单封邮件详情 |
| `/api/mails/:id` | DELETE | 删除邮件 |
| `/api/mails/:id/read` | PATCH | 标记已读 |
| `/api/parsed_mails` | GET | 服务端解析后的邮件（subject/text/html/附件） |
| `/api/settings` | GET | 站点配置（域名、前缀、开关等） |
| `/api/auto_reply` | GET/POST | 自动回复配置 |
| `/api/webhook/settings` | GET/POST | Webhook 配置 |

> `/api/new_address`、`/api/settings`、`/api/send_mail` 这三个走的是特殊鉴权分支，其余 `/api/*` 全部走 JWT 校验，缺 token 返回 `401`。

---

## 5. 相关环境变量怎么改

**不要去 Cloudflare 控制台改环境变量**——下次部署会被 `wrangler.toml` 覆盖。正确路径是改 GitHub 仓库的 Secrets / Variables，然后重跑 `Deploy Temp Email` 工作流（`cloudflare` 分支）。

| 想改什么 | 改哪里 |
| --- | --- |
| 收信域名 | Variable `MAIL_DOMAINS`（JSON 数组） |
| 随机子域名长度 | Variable `RANDOM_SUBDOMAIN_LENGTH` |
| 用户名前缀 `tmp` | Variable `ADDRESS_PREFIX` |
| 关闭匿名发号 | Variable `DISABLE_ANONYMOUS_USER_CREATE_EMAIL=true` |
| 开启邮箱独立密码 | Variable `ENABLE_ADDRESS_PASSWORD=true` |
| 管理端密码 | Secret `ADMIN_PASSWORDS`（JSON 数组） |
| JWT 签名密钥 | Secret `JWT_SECRET`（**改了会让所有已发放邮箱的 token 失效**） |

仓库入口：`https://github.com/OllieGo/cloudflare_temp_email/settings/secrets/actions`

---

## 6. 批量发放

`scripts/issue_address.py`（已在仓库 `cloudflare` 分支）封装了批量调用，支持随机子域名，输出 jsonl / csv：

```bash
# 匿名接口（/api/new_address，带 tmp 前缀，无需密码）
python3 scripts/issue_address.py \
  --base-url https://mail.ventiecho.com \
  --mode public \
  --count 100 \
  --random-subdomain \
  --out addresses.jsonl

# 管理端接口（/admin/new_address，不带前缀，需密码）
python3 scripts/issue_address.py \
  --base-url https://mail.ventiecho.com \
  --mode admin --admin-auth <密码> \
  --count 100 --random-subdomain --out addresses.jsonl
```

常用参数：`--count` 数量、`--name` 固定用户名（留空随机）、`--domain` 指定域名、`--enable-prefix` 加 `tmp` 前缀、`--no-random-subdomain` 关闭随机子域名、`--workers` 并发数（默认 8）、`--out` 输出 `.jsonl` 或 `.csv`。
默认 `--mode admin` 且随机子域名已开启。

## 7. 安全提醒

- `/api/new_address` **默认对全网开放**，任何人都能无限发号。要收紧就设 `DISABLE_ANONYMOUS_USER_CREATE_EMAIL=true`（改成登录用户才能建号），或在 Cloudflare 侧挂 Rate limiting / WAF 规则。
- 别把 `jwt` 泄露给第三方——它等价于该邮箱的完整访问权。
- 子域名解决的是**地址隔离**，不是抗封。黑名单通常按注册域判定，`ventiecho.com` 被拉黑时子域名大概率一起失效；真要抗封得再加备用根域名填进 `MAIL_DOMAINS`，每个都配一遍 Email Routing。
