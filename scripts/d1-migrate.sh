#!/usr/bin/env bash
# 执行 D1 迁移：先 db/schema.sql，再按文件名顺序跑 db/*.sql 补丁
# 必须在 worker/wrangler.toml 生成之后执行（使用 DB binding）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/worker"

WRANGLER="node_modules/.bin/wrangler"
if [[ ! -x "${WRANGLER}" ]]; then
    WRANGLER="npx --yes wrangler@4"
fi

echo "::group::D1 迁移 - schema.sql"
if ! ${WRANGLER} d1 execute DB --remote --yes --file="${ROOT}/db/schema.sql"; then
    echo "::error::db/schema.sql 执行失败"
    exit 1
fi
echo "::endgroup::"

# 补丁文件按文件名排序（日期前缀 = 时间顺序）
shopt -s nullglob
for f in $(ls "${ROOT}"/db/*.sql | grep -v 'schema.sql' | sort); do
    name="$(basename "${f}")"
    echo "::group::D1 迁移 - ${name}"
    # 历史补丁不保证幂等（如裸 ALTER TABLE），失败只告警不中断
    if ${WRANGLER} d1 execute DB --remote --yes --file="${f}"; then
        echo "✅ ${name}"
    else
        echo "::warning::${name} 执行失败（大概率是已应用过的非幂等补丁），已跳过"
    fi
    echo "::endgroup::"
done

echo "::group::D1 表结构校验"
${WRANGLER} d1 execute DB --remote --yes \
    --command="SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" || true
echo "::endgroup::"
