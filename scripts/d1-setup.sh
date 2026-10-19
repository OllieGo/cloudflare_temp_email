#!/usr/bin/env bash
# 查找（或创建）D1 数据库，并把 database_id 写回 $GITHUB_ENV
# 依赖: curl, jq (GitHub Actions ubuntu-latest 自带)
set -euo pipefail

: "${CLOUDFLARE_ACCOUNT_ID:?缺少 CLOUDFLARE_ACCOUNT_ID}"
: "${CLOUDFLARE_API_TOKEN:?缺少 CLOUDFLARE_API_TOKEN}"

DB_NAME="${D1_DATABASE_NAME:-cloudflare_temp_email}"
API="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/d1/database"

echo "::group::D1 数据库: ${DB_NAME}"

if [[ -n "${D1_DATABASE_ID:-}" ]]; then
    echo "使用已指定的 D1_DATABASE_ID=${D1_DATABASE_ID}"
else
    echo "查询已有 D1 数据库列表..."
    LIST="$(curl -sS -X GET "${API}?per_page=100" \
        -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
        -H "Content-Type: application/json")"

    if ! echo "${LIST}" | jq -e '.success == true' >/dev/null 2>&1; then
        echo "::error::读取 D1 列表失败。请确认 API Token 具备 Account -> D1 -> Edit 权限；"
        echo "::error::若不想给 D1 权限，可在仓库 Variables 里直接填 D1_DATABASE_ID 绕过本步骤。"
        echo "${LIST}"
        exit 1
    fi

    D1_DATABASE_ID="$(echo "${LIST}" | jq -r --arg n "${DB_NAME}" \
        '.result[] | select(.name == $n) | .uuid' | head -n 1)"

    if [[ -z "${D1_DATABASE_ID}" || "${D1_DATABASE_ID}" == "null" ]]; then
        echo "未找到同名数据库，正在创建..."
        CREATE="$(curl -sS -X POST "${API}" \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            --data "$(jq -nc --arg n "${DB_NAME}" '{name: $n}')")"

        if ! echo "${CREATE}" | jq -e '.success == true' >/dev/null 2>&1; then
            echo "::error::创建 D1 数据库失败"
            echo "${CREATE}"
            exit 1
        fi

        D1_DATABASE_ID="$(echo "${CREATE}" | jq -r '.result.uuid')"
        echo "✅ 已创建 D1 数据库 ${DB_NAME} -> ${D1_DATABASE_ID}"
    else
        echo "✅ 复用已有 D1 数据库 ${DB_NAME} -> ${D1_DATABASE_ID}"
    fi
fi

echo "::endgroup::"

if [[ -z "${D1_DATABASE_ID}" || "${D1_DATABASE_ID}" == "null" ]]; then
    echo "::error::未能获取 D1 database id"
    exit 1
fi

if [[ -n "${GITHUB_ENV:-}" ]]; then
    echo "D1_DATABASE_ID=${D1_DATABASE_ID}" >> "${GITHUB_ENV}"
fi
export D1_DATABASE_ID
