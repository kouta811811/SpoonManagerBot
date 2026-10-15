# AWS Infrastructure

## Scope

`foundation.yaml` creates the cost-controlled `dev` foundation required before the Spoon API contract and worker image are available:

- VPC with two public subnets and an Internet Gateway
- outbound HTTPS-only worker Security Group, with no inbound rules
- DynamoDB On-Demand state table with TTL
- ECS Cluster with no Service and no running Tasks
- ECS task execution/task IAM roles scoped to the table, log group, and SSM path
- CloudWatch Log Group with seven-day retention

It deliberately does **not** create:

- Fargate Task Definition or running Task
- OAuth callback/API Gateway
- SSM secret values
- ECS Service, NAT Gateway, ALB, ECR repository, or paid custom metrics
- production resources

This makes the foundation safe to deploy before Spoon OAuth URLs, permissions, event schemas, and the container digest are finalized. The foundation itself has no continuously running compute. DynamoDB and logs remain usage-priced.

## Deploy

Prerequisites:

- AWS CLI v2
- permission to create CloudFormation, VPC, ECS, DynamoDB, Logs, and IAM resources
- explicit confirmation of the target AWS account

```bash
EXPECTED_AWS_ACCOUNT_ID='<12-digit-account-id>' \
./scripts/deploy-foundation.sh
```

Defaults:

- region: `ap-northeast-1`
- stack: `spoon-manager-bot-dev-foundation`
- environment: `dev`

Override with `AWS_REGION`, `STACK_NAME`, `PROJECT_NAME`, and `ENVIRONMENT`.

## Inspect

```bash
aws cloudformation describe-stacks \
  --region ap-northeast-1 \
  --stack-name spoon-manager-bot-dev-foundation \
  --query 'Stacks[0].Outputs' \
  --output table
```

## Delete

Deletion removes the development table and log group as well as network and ECS resources. Export any non-expired state first if it must be retained.

```bash
aws cloudformation delete-stack \
  --region ap-northeast-1 \
  --stack-name spoon-manager-bot-dev-foundation
```

No secrets are accepted as CloudFormation parameters. OAuth secret values will be inserted separately under `/spoon-manager-bot/dev/` after the official contract and application registration are ready.
