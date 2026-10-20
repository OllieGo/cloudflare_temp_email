#!/usr/bin/env node
/**
 * 根据环境变量生成 worker/wrangler.toml
 * 由 GitHub Actions 调用，本地也可直接运行：
 *   MAIL_DOMAINS='["ventiecho.com"]' node scripts/gen-wrangler.mjs
 *
 * 说明：worker/wrangler.toml 已被 worker/.gitignore 忽略，不会进仓库，
 * 因此 JWT 等敏感值可以安全地写在这里并推送到 Cloudflare。
 */
import { randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, "..", "worker", "wrangler.toml");

/* ------------------------- 环境变量读取工具 ------------------------- */
const env = (k, d = "") => {
    const v = process.env[k];
    return v === undefined || v === null ? d : String(v).trim();
};

const bool = (k, d = false) => {
    const v = env(k).toLowerCase();
    if (v === "") return d;
    return v === "true" || v === "1" || v === "yes" || v === "on";
};

const int = (k, d) => {
    const v = env(k);
    if (v === "") return d;
    const n = Number.parseInt(v, 10);
    return Number.isNaN(n) ? d : n;
};

const list = (k, d = []) => {
    const v = env(k);
    if (!v) return d;
    try {
        const parsed = JSON.parse(v);
        if (Array.isArray(parsed)) return parsed.map((x) => String(x).trim()).filter(Boolean);
    } catch { /* 不是 JSON，走逗号分隔 */ }
    return v.split(",").map((s) => s.trim()).filter(Boolean);
};

const rand = (n = 24) => randomBytes(n).toString("base64url").replace(/[^a-zA-Z0-9]/g, "").slice(0, n);

/** TOML basic string 转义 */
const q = (v) => String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
/** TOML 字符串数组 */
const qa = (arr) => `[${arr.map((x) => `"${q(x)}"`).join(", ")}]`;

/* ------------------------------ 配置 ------------------------------ */
const workerName = env("WORKER_NAME", "cloudflare-temp-email");
const mailDomains = list("MAIL_DOMAINS", list("DOMAINS", []));
if (mailDomains.length === 0) {
    console.error("::error::MAIL_DOMAINS 为空，请设置收信根域名（例如 ventiecho.com）");
    process.exit(1);
}

const defaultDomains = list("DEFAULT_DOMAINS", mailDomains);
const randomSubdomainDomains = list("RANDOM_SUBDOMAIN_DOMAINS", mailDomains);
const routes = list("WORKER_ROUTES", []);
const d1Name = env("D1_DATABASE_NAME", "cloudflare_temp_email");
const d1Id = env("D1_DATABASE_ID", "");
if (!d1Id) {
    console.error("::error::D1_DATABASE_ID 为空，D1 建库/查库步骤没有产出 database id");
    process.exit(1);
}

// 敏感值：未配置则自动生成，避免首次部署后无法登录
const jwtSecret = env("JWT_SECRET") || (console.log("::warning::未配置 JWT_SECRET，已随机生成；请尽快在仓库 Secrets 中固定该值，否则重新部署会让已发放邮箱的 JWT 失效"), rand(32));
const adminPasswords = list("ADMIN_PASSWORDS", []);
const generatedAdminPassword = adminPasswords.length === 0 ? rand(12) : null;
const finalAdminPasswords = adminPasswords.length ? adminPasswords : [generatedAdminPassword];
// 站点访问密码：默认关闭（网页端开放，不弹密码框，防盗刷交给 Cloudflare WAF 限流）。
// 需要全站上锁时，在 deploy.yml 的 env 里传 ENABLE_SITE_PASSWORD=true 即可，无需改代码。
// 注意：关闭时也要显式写入空数组——wrangler 不会删除 toml 里移除的变量，否则上次部署的值会残留。
const sitePasswordEnabled = bool("ENABLE_SITE_PASSWORD", false);
const sitePasswords = sitePasswordEnabled ? list("SITE_PASSWORDS", []) : [];
if (!sitePasswordEnabled && list("SITE_PASSWORDS", []).length) {
    console.log("::notice::已配置 SITE_PASSWORDS 但 ENABLE_SITE_PASSWORD 未开启，本次部署不启用站点密码（网页端开放）");
}

const useAssets = bool("USE_WORKER_ASSETS", true);
const enableSubdomainMatch = bool("ENABLE_CREATE_ADDRESS_SUBDOMAIN_MATCH", true);
const cleanupCron = env("CLEANUP_CRON", "");
const prefix = env("ADDRESS_PREFIX", "tmp");

/* ------------------------------ 生成 ------------------------------ */
const L = [];
L.push("# 该文件由 scripts/gen-wrangler.mjs 自动生成，请勿手动编辑");
L.push(`name = "${q(workerName)}"`);
L.push(`main = "${q(env("WORKER_MAIN", "src/worker.ts"))}"`);
L.push(`compatibility_date = "${q(env("COMPATIBILITY_DATE", "2025-04-01"))}"`);
L.push(`compatibility_flags = [ "nodejs_compat" ]`);
L.push("keep_vars = true");
L.push("");

if (routes.length) {
    // 显式带上 zone_name，避免 Token 有多个/受限 zone 时报 "Can't infer zone from route"(10082)
    const zoneName = env("WORKER_ROUTE_ZONE", mailDomains[0]);
    L.push("routes = [");
    for (const r of routes) {
        L.push(`\t{ pattern = "${q(r)}", custom_domain = true, zone_name = "${q(zoneName)}" },`);
    }
    L.push("]");
    L.push("");
}

if (useAssets) {
    L.push("[assets]");
    L.push(`directory = "${q(env("ASSETS_DIR", "../frontend/dist/"))}"`);
    L.push('binding = "ASSETS"');
    L.push("run_worker_first = true");
    L.push("");
}

if (cleanupCron) {
    L.push("[triggers]");
    L.push(`crons = [ "${q(cleanupCron)}" ]`);
    L.push(`CLEANUP_BATCH_SIZE = ${int("CLEANUP_BATCH_SIZE", 3000)}`);
    L.push("");
}

/* ---- [vars] ---- */
const V = [];
const put = (k, v) => V.push(`${k} = ${v}`);
const putStr = (k, v) => put(k, `"${q(v)}"`);
const putBool = (k, v) => put(k, String(v));

putStr("PREFIX", prefix);
putStr("TITLE", env("SITE_TITLE", "临时邮箱"));
putStr("DEFAULT_LANG", env("DEFAULT_LANG", "zh"));
if (env("ANNOUNCEMENT")) putStr("ANNOUNCEMENT", env("ANNOUNCEMENT"));
put("DEFAULT_DOMAINS", qa(defaultDomains));
put("DOMAINS", qa(mailDomains));
putBool("ENABLE_CREATE_ADDRESS_SUBDOMAIN_MATCH", enableSubdomainMatch);
put("RANDOM_SUBDOMAIN_DOMAINS", qa(randomSubdomainDomains));
put("RANDOM_SUBDOMAIN_LENGTH", int("RANDOM_SUBDOMAIN_LENGTH", 8));
putStr("JWT_SECRET", jwtSecret);
// 始终写入：空数组 = 关闭站点密码，同时覆盖上一次部署残留的值
put("PASSWORDS", qa(sitePasswords));
put("ADMIN_PASSWORDS", qa(finalAdminPasswords));
if (env("ADMIN_CONTACT")) putStr("ADMIN_CONTACT", env("ADMIN_CONTACT"));
putBool("ENABLE_USER_CREATE_EMAIL", bool("ENABLE_USER_CREATE_EMAIL", true));
putBool("ENABLE_USER_DELETE_EMAIL", bool("ENABLE_USER_DELETE_EMAIL", true));
putBool("ENABLE_AUTO_REPLY", bool("ENABLE_AUTO_REPLY", false));
putBool("ENABLE_ADDRESS_PASSWORD", bool("ENABLE_ADDRESS_PASSWORD", false));
putBool("ENABLE_AI_EMAIL_EXTRACT", bool("ENABLE_AI_EMAIL_EXTRACT", true));
putStr("AI_EXTRACT_MODE", env("AI_EXTRACT_MODE", "local"));
putBool("CREATE_ADDRESS_DEFAULT_DOMAIN_FIRST", bool("CREATE_ADDRESS_DEFAULT_DOMAIN_FIRST", false));
if (env("FRONTEND_URL")) putStr("FRONTEND_URL", env("FRONTEND_URL"));
if (env("BACKEND_URL")) putStr("BACKEND_URL", env("BACKEND_URL"));
if (env("MIN_ADDRESS_LEN")) put("MIN_ADDRESS_LEN", int("MIN_ADDRESS_LEN", 1));
if (env("MAX_ADDRESS_LEN")) put("MAX_ADDRESS_LEN", int("MAX_ADDRESS_LEN", 30));
if (env("ADDRESS_CHECK_REGEX")) putStr("ADDRESS_CHECK_REGEX", env("ADDRESS_CHECK_REGEX"));
if (env("TG_BOT_TOKEN")) putStr("TG_BOT_TOKEN", env("TG_BOT_TOKEN"));

L.push("[vars]");
L.push(...V);
L.push("");

/* ---- 绑定 ---- */
L.push("[[d1_databases]]");
L.push('binding = "DB"');
L.push(`database_name = "${q(d1Name)}"`);
L.push(`database_id = "${q(d1Id)}"`);
L.push("");

if (env("KV_NAMESPACE_ID")) {
    L.push("[[kv_namespaces]]");
    L.push('binding = "KV"');
    L.push(`id = "${q(env("KV_NAMESPACE_ID"))}"`);
    L.push("");
}

if (bool("ENABLE_WORKERS_AI", false)) {
    L.push("[ai]");
    L.push('binding = "AI"');
    L.push("");
}

if (bool("ENABLE_RATE_LIMIT", false)) {
    L.push("[[unsafe.bindings]]");
    L.push('name = "RATE_LIMITER"');
    L.push('type = "ratelimit"');
    L.push(`namespace_id = "${q(env("RATE_LIMIT_NAMESPACE_ID", "1001"))}"`);
    L.push(`simple = { limit = ${int("RATE_LIMIT_MAX", 60)}, period = ${int("RATE_LIMIT_PERIOD", 60)} }`);
    L.push("");
}

writeFileSync(OUT, L.join("\n"), "utf-8");

/* ------------------------------ 输出 ------------------------------ */
console.log(`✅ 已生成 ${OUT}`);
console.log(`   worker       : ${workerName}`);
console.log(`   DOMAINS      : ${mailDomains.join(", ")}`);
console.log(`   随机子域名   : ${randomSubdomainDomains.join(", ")} (长度 ${int("RANDOM_SUBDOMAIN_LENGTH", 8)})`);
console.log(`   子域名后缀匹配: ${enableSubdomainMatch}`);
console.log(`   D1           : ${d1Name} / ${d1Id}`);
console.log(`   前端静态资源 : ${useAssets}`);
console.log(`   路由         : ${routes.length ? routes.join(", ") : "(workers.dev 默认域名)"}`);
if (generatedAdminPassword) {
    console.log("");
    console.log(`⚠️  未配置 ADMIN_PASSWORDS，本次随机生成管理后台密码: ${generatedAdminPassword}`);
    console.log("    请把它写入仓库 Secret ADMIN_PASSWORDS（JSON 数组，如 [\"你的密码\"]），否则下次部署会变。");
}
