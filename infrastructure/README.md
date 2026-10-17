# AWS Infrastructure

## Stacks

| Stack | Template | Contents |
|---|---|---|
| `spoon-manager-bot-dev-foundation` | `foundation.yaml` | VPC (2 public subnets, IGW), outbound-HTTPS-only security group, DynamoDB state table (On-Demand, TTL), ECR repository (private, 5 images kept), ECS cluster (no Service), task execution/task IAM roles, worker log group (7 days) |
| `spoon-manager-bot-dev-app` | `app.yaml` | Fargate task definition (ARM64, 0.25 vCPU / 0.5 GB, no Service), OAuth callback Lambda + HTTP API (`GET /oauth/callback`), Control Lambda (start/stop, IAM-only) |
| `spoon-manager-bot-github-oidc` | `github-oidc-bootstrap.yaml` | GitHub OIDC provider and the scoped deploy role (run once by an admin) |

One container image is used for the worker and both Lambdas. Nothing runs continuously: a Fargate task exists only between `start` and the end of the broadcast. No NAT Gateway, ALB, ECS Service, Secrets Manager, or production resources are created.

## First-time setup (dev)

### 1. Apply the bootstrap (admin, once — and again whenever it changes)

The deploy role needs ECR, Lambda, HTTP API, task-definition, and `iam:PassRole` permissions for the app stack. Re-run this with an administrator identity before the first app deployment:

```bash
aws cloudformation deploy \
  --region ap-northeast-1 \
  --stack-name spoon-manager-bot-github-oidc \
  --template-file infrastructure/github-oidc-bootstrap.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GitHubOwner=kouta811811 \
    GitHubRepo=SpoonManagerBot \
    GitHubEnvironment=dev \
    CreateOidcProvider=false \
    ExistingOidcProviderArn=arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com
```

(Use `CreateOidcProvider=true` and omit the ARN only if the provider does not exist yet.) The role still cannot run tasks or read secret values.

### 2. Deploy

Merging to `main` (or running **Deploy dev** manually) deploys the foundation, builds and pushes the arm64 image tagged with the commit SHA, and deploys the app stack pinned to the image digest. The job prints:

- `OAuthCallbackUrl` — e.g. `https://abc123.execute-api.ap-northeast-1.amazonaws.com/oauth/callback`
- `ControlFunctionName` — `spoon-manager-bot-dev-control`

### 3. Register the Spoon Developer App

With the **bot account** (not the DJ's account), register the app in the Spoon developer center:

- Region: Japan
- Login redirect URL: exactly the `OAuthCallbackUrl` above
- Permissions: `events.chat`, `events.like`, `events.donation`, `chat.send`

### 4. Store the app credentials in SSM

```bash
P=/spoon-manager-bot/dev
aws ssm put-parameter --type SecureString --name "$P/oauth/client_id"     --value '<Client ID>'
aws ssm put-parameter --type SecureString --name "$P/oauth/client_secret" --value '<Client Secret>'
aws ssm put-parameter --type SecureString --name "$P/oauth/redirect_uri"  --value '<OAuthCallbackUrl>'
```

Never pass these as CloudFormation parameters or commit them.

### 5. Get the DJ's consent

Run from a checkout with AWS credentials:

```bash
export AWS_REGION=ap-northeast-1 STATE_TABLE=spoon-manager-bot-dev-state SSM_PREFIX=/spoon-manager-bot/dev/
PYTHONPATH=src python3 -m spoon_manager_bot.cli auth-url
```

Send the printed URL to the DJ (valid 10 minutes, single use). After the DJ approves, the browser shows 「連携しました」 and the tokens are stored under `$P/token/`.

## Running a broadcast

```bash
FN=spoon-manager-bot-dev-control
invoke() { aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out \
  --payload "{\"action\":\"$1\"}" /dev/stdout; echo; }

invoke start    # start one worker (waits up to 10 minutes for the DJ to go live)
invoke status   # running / stale / not_running
invoke stop     # stop the worker
invoke reset    # clear a leftover lease after the task is confirmed STOPPED
```

The worker stops by itself when the broadcast ends (`end.reason=LIVE_ENDED`). While connected, Spoon posts a listener notice under the bot's name — this also happens in dev, because dev connects to the DJ's real broadcast.

Logs: CloudWatch Logs group `/aws/ecs/spoon-manager-bot-dev-worker` (JSON lines; no chat text, nicknames, listener ids, or tokens).

Worker exit codes: `0` normal (live ended, wait timeout, stop), `2` needs an operator (re-consent, DJ blocked the bot, repeated failures), `1` configuration error.

## Disconnecting

```bash
PYTHONPATH=src python3 -m spoon_manager_bot.cli revoke                     # revoke and delete tokens
PYTHONPATH=src python3 -m spoon_manager_bot.cli revoke --delete-templates  # also delete thank-you messages
```

## Deleting

Delete the app stack first, then the foundation. Deleting the foundation removes the state table (including registered thank-you messages), the image repository, and logs.

```bash
aws cloudformation delete-stack --region ap-northeast-1 --stack-name spoon-manager-bot-dev-app
aws cloudformation delete-stack --region ap-northeast-1 --stack-name spoon-manager-bot-dev-foundation
```

SSM parameters under `/spoon-manager-bot/dev/` are not managed by CloudFormation; delete them separately.

## CI

`.github/workflows/ci.yml` runs on pull requests: cfn-lint, Python lint/types/tests, an arm64 image build with an import smoke test, and guardrails. It never touches AWS.
