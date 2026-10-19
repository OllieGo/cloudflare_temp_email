# 临时邮箱部署手册（GitHub Actions 版）

> 本仓库在官方 `Deploy Backend` 之外，新增了 **`Deploy Temp Email`** 一键部署工作流：  
> D1 自动建库/迁库 → 自动生成 `wrangler.toml` → 部署 Worker（含前端静态资源）。  
> 你不再需要手工拼 `BACKEND_TOML` 这个巨型 Secret。

根域名以 `ventiecho.com` 为例，Worker 对外域名以 `mail.ventiecho.com` 为例，请按实际替换。

---

## 0.1 分支策略（fork 用户必读）

上游仓库自带一条 `Upstream Sync` 工作流，**每周一自动把你的 `main` 覆盖成上游最新版**。
所以 **定制提交绝对不能放 `main`**，否则会被静默冲掉。约定如下：

| 分支 | 角色 | 谁在写 |
| --- | --- | --- |
| `main` | 上游镜像，保持与 `dreamhunter2333/cloudflare_temp_email` 一致 | `Upstream Sync` 自动同步／GitHub 的 Sync fork 按钮 |
| `dev` | 你的定制分支，**也是部署触发分支** | 你（和我） |

### 上游有更新时怎么合并

```
上游 dreamhunter2333 ──(Upstream Sync, 每周一)──▶ main ──(PR: main → dev)──▶ dev ──▶ 自动部署
```

1. `main` 由上游同步工作流自动更新（也可以手动点仓库首页的 `Sync fork`）。
2. `Merge Upstream Into Dev` 工作流检测到 `main` 领先 `dev` 后，**自动开一个 PR**（标题 `chore: 合并上游更新 (main → dev)`）。
   已存在同名 PR 时不会重复创建，只会追加一条评论。
3. 你 review 这个 PR：冲突通常集中在 `scripts/`、`DEPLOY.zh-CN.md`、`.github/workflows/deploy.yml` 这几个你改过的文件。
4. 合并 PR → `dev` 触发 `Deploy Temp Email` → 自动重新部署。

> 想手动合并也可以：
>
> ```bash
> git checkout dev
> git fetch upstream main
> git merge upstream/main        # 或 git merge origin/main
> # 解决冲突后 git push origin dev
> ```

### 首次切换到这套结构的命令

```bash
git checkout -b dev main        # 从当前 main 切出 dev
git push -u origin dev          # 推上去
# main 还原成纯上游镜像（会丢掉你在 main 上的定制提交，它们已经在 dev 里了）
git fetch upstream main
git checkout main && git reset --hard upstream/main
git push --force-with-lease origin main
```

> ⚠️ **默认分支保持 `main`，不要改成 `dev`。**
> 上游仓库自己也有一条 `dev` 分支（跟你的定制毫无关系，别去同步它）。
> GitHub 的 `Sync fork` 按钮只同步默认分支，默认分支留在 `main` 才能让 `Upstream Sync`
> 干净地把 `main` 对齐上游；改成 `dev` 反而可能把上游的 `dev` 混进来。
>
> 代价是：手动开 PR 时 GitHub 默认基准是 `main`，记得手动把 base 切成 `dev`。

---

## 0. 架构

```
收件方 → *@*.ventiecho.com
           ↓ (Cloudflare Email Routing Catch-all)
        Worker: cloudflare-temp-email
           ├─ 前端静态资源（Vue，同源 /api）
           └─ D1 数据库（地址 / 邮件 / 用户）
```

- 一个 Worker 同时提供 **网页端 + API**，前端与后端同源，无需配 CORS / Pages。
- 邮箱数量不受限：地址只是 D1 里的一行记录，靠 `/api/new_address` 或 `/admin/new_address` 发放。

---

## 1. 前置条件（Cloudflare 侧必做，缺一不可）

### 1.1 API Token

在 <https://dash.cloudflare.com/profile/api-tokens> 创建自定义 Token，至少包含：

| 权限                         | 级别                 |
| -------------------------- | ------------------ |
| Account → Workers Scripts  | Edit               |
| Account → D1               | Edit               |
| Account → Cloudflare Pages | Edit（不用 Pages 可不给） |
| Zone → DNS                 | Edit（用自定义域名时需要）    |
| Zone → Email Routing       | Edit               |

Account ID 在控制台右侧栏，或从任意控制台 URL 里取：`dash.cloudflare.com/<ACCOUNT_ID>/...`

### 1.2 Email Routing（收信的地基）

对 `ventiecho.com`：

1. `Email Routing` → 启用，按向导下发 `Email DNS records`（MX / SPF / DKIM / DMARC）。
2. 至少验证一个**目标邮箱地址**（destination address）。
3. `Routing rules` → **Catch-all address** → 动作选 `Send to Worker` → 选中 `cloudflare-temp-email`。

> ⚠️ 这一步没做，邮件永远进不了 Worker，网页上会一直收不到验证码。

### 1.3 通配 MX（子域名无限邮箱的关键）

Cloudflare Email Routing **不会把 apex 配置继承给子域名**，`*.ventiecho.com` 想收信只有两条路：  
控制台逐个 "Add subdomain"（不适合随机子域名），或者 **DNS 通配 MX**（推荐）。

在 `ventiecho.com` 的 DNS 里，把 apex 现有的**每一条 MX 记录**原样复制一份到主机名 `*`（保留 priority 与 target），例如：

| 类型 | 名称              | 内容                         | 优先级 |
| -- | --------------- | -------------------------- | --- |
| MX | `ventiecho.com` | `route1.mx.cloudflare.net` | 21  |
| MX | `ventiecho.com` | `route2.mx.cloudflare.net` | 48  |
| MX | `*`             | `route1.mx.cloudflare.net` | 21  |
| MX | `*`             | `route2.mx.cloudflare.net` | 48  |

这样 `*.ventiecho.com` 与 apex 落到同一个 Email Routing zone，由 apex 的 Catch-all 交给 Worker 处理，CF 侧无需额外配置。

### 1.4 用脚本一键收尾（推荐）

上面 1.2 / 1.3 的手工操作，仓库里已经有脚本代劳：

```bash
export CLOUDFLARE_API_TOKEN=xxx
export CLOUDFLARE_ACCOUNT_ID=1cd574f79c73c28ba9000f98a492abbd
export MAIL_DOMAINS='["ventiecho.com"]'
python3 scripts/setup_email_routing.py
```

脚本按顺序做三件事，任何一步失败只告警、不中断：

1. 尝试调用 API 启用 Email Routing（**需要 Token 带 `Zone → Email Routing → Edit`**；没有权限会提示去控制台点一下）
2. 把 apex 的 MX 原样复制成主机名 `*` 的通配 MX（已存在则跳过，幂等）
3. 把 Catch-all 规则改成 `Send to Worker → cloudflare-temp-email`

> 常见坑：Email Routing 还没启用时，第 3 步会返回 `409 Invalid rule operation`。
> 去控制台 `Email Routing → 启用`，再重跑脚本即可。

---

## 2. GitHub 仓库配置

`Settings → Secrets and variables → Actions`

### Secrets（必填 2 个）

| Name                    | 说明                    |
| ----------------------- | --------------------- |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID |
| `CLOUDFLARE_API_TOKEN`  | 上面创建的 API Token       |

### Secrets（强烈建议补上，否则每次部署都会变）

| Name              | 说明                                    | 示例                |
| ----------------- | ------------------------------------- | ----------------- |
| `JWT_SECRET`      | 签发地址 JWT 的密钥，变了会让已发放邮箱的 token 全部失效    | 32 位随机串           |
| `ADMIN_PASSWORDS` | 管理后台 `/admin` 密码，**JSON 数组**          | `["MyStrongPwd"]` |
| `SITE_PASSWORDS`  | 站点私密密码，设了之后全站要带 `x-custom-auth`，不设则公开 | `["123456"]`      |

### Variables（可选，都有默认值）

| Name                                    | 默认值                      | 说明                                              |
| --------------------------------------- | ------------------------ | ----------------------------------------------- |
| `WORKER_NAME`                           | `cloudflare-temp-email`  | Worker 服务名                                      |
| `MAIL_DOMAINS`                          | `["ventiecho.com"]`      | 允许的全部收信域名                                       |
| `DEFAULT_DOMAINS`                       | 同 `MAIL_DOMAINS`         | 未登录用户可用域名                                       |
| `RANDOM_SUBDOMAIN_DOMAINS`              | 同 `MAIL_DOMAINS`         | 允许生成随机子域名的基域                                    |
| `RANDOM_SUBDOMAIN_LENGTH`               | `8`                      | 随机子域名长度                                         |
| `WORKER_ROUTES`                         | `["mail.ventiecho.com"]` | Worker 自定义域名（会自动建 DNS 记录）；清空则只用 `*.workers.dev` |
| `ADDRESS_PREFIX`                        | `tmp`                    | 邮箱名前缀，不需要就设空                                    |
| `ENABLE_CREATE_ADDRESS_SUBDOMAIN_MATCH` | `true`                   | 允许 API 直接指定任意子域名                                |
| `D1_DATABASE_NAME`                      | `cloudflare_temp_email`  | D1 库名，**已存在的库会被自动复用**                           |
| `D1_DATABASE_ID`                        | 空                        | 直接指定 database id，跳过查找                           |
| `CLEANUP_CRON`                          | 空                        | 定时清理，如 `0 0 * * *`                              |

---

## 3. 部署

`Actions → Deploy Temp Email → Run workflow`（分支选 `main`）→ 等待完成。

流程内部依次执行：

1. 校验 Secrets
2. `worker/` 安装依赖（提供 wrangler）
3. **D1 建库或复用**（同名已存在则直接用你现有的库），输出 `database_id`
4. 构建前端 → `frontend/dist`
5. 生成 `worker/wrangler.toml`
6. 执行 `db/schema.sql` + 所有 `db/*.sql` 补丁（历史补丁不幂等，失败只告警）
7. `wrangler deploy`

之后每次 `git push` 到 `main` 且改动涉及 `worker/`、`frontend/`、`db/`、`scripts/` 会自动重跑。

> 官方的 `Deploy Backend` / `Deploy Frontend with page function` 工作流依赖 `BACKEND_TOML` / `PAGE_TOML` Secret，  
> 没配就自动跳过，不会冲突。

---

## 4. 子域名「无限邮箱」

两种能力，默认都已打开：

| 能力    | 开关                                             | 效果                                                                     |
| ----- | ---------------------------------------------- | ---------------------------------------------------------------------- |
| 随机子域名 | `RANDOM_SUBDOMAIN_DOMAINS`                     | `name@x7k2p9q1.ventiecho.com`，前端可勾、API 传 `enableRandomSubdomain: true` |
| 指定子域名 | `ENABLE_CREATE_ADDRESS_SUBDOMAIN_MATCH = true` | API 可直接创建 `name@team.ventiecho.com`、`name@dev.team.ventiecho.com`      |

**注意**：程序只能生成地址，能不能收到邮件取决于第 1.3 节的通配 MX。前端生成成功但收不到信，九成是通配 MX 没配。

域名匹配规则：`domain === 允许域名 || domain.endsWith("." + 允许域名)`，所以 `badventiecho.com` 不会被误判为合法。

---

## 5. 后端自动发放

### 5.1 管理端 API（推荐，可批量）

```bash
curl -X POST https://mail.ventiecho.com/admin/new_address \
  -H 'x-admin-auth: 你的管理后台密码' \
  -H 'Content-Type: application/json' \
  -d '{"name":"project001","domain":"ventiecho.com","enablePrefix":true,"enableRandomSubdomain":true}'
```

返回 `{"jwt":"...","address":"tmpproject001@x7k2p9q1.ventiecho.com","address_id":123}`

### 5.2 匿名公开 API

```bash
curl -X POST https://mail.ventiecho.com/api/new_address \
  -H 'Content-Type: application/json' \
  -d '{"enableRandomSubdomain":true}'
```

需要 `ENABLE_USER_CREATE_EMAIL = true`（默认开）；开了站点密码的请求要带 `x-custom-auth`。

### 5.3 批量脚本

```bash
python scripts/issue_address.py \
  --base-url https://mail.ventiecho.com \
  --admin-auth "$ADMIN_PASSWORD" \
  --domain ventiecho.com \
  --count 50 --random-subdomain \
  --out addresses.csv
```

纯标准库实现，无依赖；`--out` 支持 `.jsonl` / `.csv`。

### 5.4 读取邮件

```bash
curl https://mail.ventiecho.com/api/mails \
  -H 'Authorization: Bearer <上面返回的 jwt>'
```

---

## 6. 验收与排障

| 现象                   | 排查                                                                            |
| -------------------- | ----------------------------------------------------------------------------- |
| 部署成功但打不开页面           | 检查 `WORKER_ROUTES` 域名 DNS 是否指向 CF；`*.workers.dev` 在中国大陆不可达，必须用自定义域名           |
| 能建邮箱但收不到信            | ① Email Routing Catch-all 是否指向 Worker ② 通配 MX `*` 是否配齐 ③ 该子域是否在允许列表           |
| `/admin` 登录失败        | 首次部署若没设 `ADMIN_PASSWORDS`，密码是随机生成的，去看 Actions 日志里 `随机生成管理后台密码` 那行，然后写进 Secret |
| 重新部署后老邮箱 token 失效    | `JWT_SECRET` 没固定，去 Secrets 里设一个固定值                                            |
| D1 报 `no such table` | 迁移没跑，重跑一次工作流并把 `run_migration` 设为 `true`                                      |
| Worker 体积超限          | 把 `with_frontend` 设为 `false`，改用 Pages 部署前端                                    |
