#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-spoon-manager-bot-dev-foundation}"
PROJECT_NAME="${PROJECT_NAME:-spoon-manager-bot}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
EXPECTED_AWS_ACCOUNT_ID="${EXPECTED_AWS_ACCOUNT_ID:-}"
TEMPLATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infrastructure/foundation.yaml"

if [[ -z "${EXPECTED_AWS_ACCOUNT_ID}" ]]; then
  echo "EXPECTED_AWS_ACCOUNT_ID is required; refusing an ambiguous deployment." >&2
  exit 2
fi

actual_account_id="$(aws sts get-caller-identity --query Account --output text)"
if [[ "${actual_account_id}" != "${EXPECTED_AWS_ACCOUNT_ID}" ]]; then
  echo "AWS account mismatch: expected ${EXPECTED_AWS_ACCOUNT_ID}, got ${actual_account_id}." >&2
  exit 2
fi

if [[ "${ENVIRONMENT}" == "prod" ]]; then
  echo "This foundation script does not deploy production resources." >&2
  exit 2
fi

aws cloudformation validate-template \
  --region "${AWS_REGION}" \
  --template-body "file://${TEMPLATE_FILE}" >/dev/null

aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${TEMPLATE_FILE}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ProjectName=${PROJECT_NAME}" \
    "Environment=${ENVIRONMENT}" \
  --tags \
    "Project=${PROJECT_NAME}" \
    "Environment=${ENVIRONMENT}" \
    "ManagedBy=CloudFormation"

aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --query 'Stacks[0].Outputs' \
  --output table
