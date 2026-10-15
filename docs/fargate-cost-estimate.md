# Fargate月額費用見積り

## 1. 結論

SSE Workerを配信中だけ1 Task起動する場合、基準単価は **約 `$0.0173/時間`** と見積もる。

標準的な利用を月80時間（週5日 × 1日4時間）とすると、Task本体は **約 `$1.39/月`**。ログ等の少額利用と10%の予備費を含む保守的な予算は **約 `$1.80/月`**（参考換算 `$1 = ¥150` で約 `¥270/月`）とする。

## 2. 見積り条件

| 項目 | 条件 |
|---|---|
| AWS Region | Asia Pacific (Tokyo), `ap-northeast-1` |
| Launch type | ECS Fargate On-Demand |
| OS / architecture | Linux / ARM64 (Graviton) |
| Task count | 配信中のみ1 Task、配信外は0 |
| CPU | 0.25 vCPU |
| Memory | 0.5 GB |
| Ephemeral storage | 既定20 GB以内（追加課金なし） |
| Network | public subnet、Taskごとにpublic IPv4 1個、NAT Gateway/ALBなし |
| Availability | Fargate Spotは使用しない |
| Currency | USD。円換算は比較用に `$1 = ¥150` と仮定 |

## 3. 使用単価

東京リージョンLinux/ARM64の取得可能な直近単価を用いる。

| 課金項目 | 単価 | Task当たり計算 | 1時間当たり |
|---|---:|---:|---:|
| Fargate ARM vCPU | `$0.04045 / vCPU-hour` | `0.25 × 0.04045` | `$0.0101125` |
| Fargate ARM memory | `$0.00442 / GB-hour` | `0.5 × 0.00442` | `$0.00221` |
| Public IPv4 | `$0.005 / IP-hour` | `1 × 0.005` | `$0.005` |
| **合計** |  |  | **`$0.0173225/hour`** |

FargateはTaskのimage pull開始から終了まで秒単位（最低1分）で課金される。停止中のTask compute料金は発生しない。ARMは同等x86 Fargateより約20%低コストである。

> AWS Price List APIへこの環境から接続できなかったため、Fargateの数値単価は2025年10月時点の東京リージョン公開比較値を使用した概算である。実装・デプロイ直前にAWS Pricing Calculatorまたは請求画面で再確認する。

## 4. 配信時間別の月額

`Task費 = 月間配信時間 × $0.0173225`

「予算目安」は、Task費に周辺サービス予備費 `$0.25/月` を加え、さらに10%の価格・使用量余裕を加えた値である。実際に必ず発生する固定費ではない。

| 利用例 | 月間Task時間 | Task + IPv4 | 予算目安 | 円換算目安 |
|---|---:|---:|---:|---:|
| 月10時間 | 10h | `$0.17` | `$0.47` | 約`¥71` |
| 月20時間 | 20h | `$0.35` | `$0.66` | 約`¥99` |
| 月40時間 | 40h | `$0.69` | `$1.04` | 約`¥156` |
| **月80時間（想定標準）** | **80h** | **`$1.39`** | **`$1.80`** | **約`¥270`** |
| 月120時間 | 120h | `$2.08` | `$2.56` | 約`¥384` |
| 月200時間 | 200h | `$3.46` | `$4.09` | 約`¥614` |
| 常時稼働相当 | 730h | `$12.65` | `$14.18` | 約`¥2,127` |

## 5. 周辺サービス

MVP・単一DJ・低トラフィックを前提とした見込み。

| サービス | 想定 | 月額見込み |
|---|---|---:|
| ECS Cluster / Control Plane | ECS自体に常駐Taskなし | `$0` |
| API Gateway HTTP API | OAuth callbackのみ、数回 | 無料枠内または`$0.01未満` |
| Lambda | OAuth callback/controlのみ | 無料枠内または`$0.01未満` |
| DynamoDB On-Demand | lease、TTL、cooldown、小量event ID | 無料枠内または数セント |
| SSM Standard Parameter | 数個、標準throughput | 追加料金なし |
| CloudWatch Logs | PIIを除外、7日保持、100 MB/月以内を目標 | 概ね`$0.10未満` |
| Internet data transfer | SSE/Chatの小量通信 | 無料枠内を想定 |
| Container registry | 公開imageをdigest固定 | `$0`想定 |

周辺サービス合計は通常 **`$0〜$0.25/月`** の範囲を目標とする。ただし無料枠の利用状況、ログ量、APIイベント数、AWS料金改定により変動する。

## 6. コスト上限策

1. ECS Serviceを作らず、配信開始時だけ `RunTask` する。
2. 配信終了・手動停止・回復不能エラー時に必ずTaskを終了する。
3. DynamoDB leaseへ最大稼働期限を持たせ、孤立Taskを検出する。
4. EventBridgeによる短周期常時pollingやNAT Gatewayを導入しない。
5. CloudWatch Logsを7日保持とし、生SSE・チャット本文を出力しない。
6. AWS Budgetsを **警告 `$3/月`、強い警告 `$5/月`** に設定する。
7. Taskごとに開始・終了・稼働秒数を記録し、月次で見積りと請求を照合する。
8. 自動コスト停止は誤停止で配信運営へ影響するためMVPでは行わず、予算通知後にOPERATORが判断する。

## 7. 承認対象

- 本番Worker: Fargate On-Demand ARM64、0.25 vCPU、0.5 GB。
- 配信中だけ1 Task、配信外0 Task。
- public IPv4の時間課金を許容し、NAT Gateway/ALBは使用しない。
- 標準利用80時間/月の予算目安: `$1.80/月`（参考約`¥270/月`）。
- AWS予算上限の運用目安: `$5/月`。
- デプロイ直前に最新単価を再確認し、20%以上上昇していた場合は再承認する。

## 8. 参照

- [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/)
- [AWS Fargate Graviton: 20% lower cost](https://aws.amazon.com/about-aws/whats-new/2021/11/aws-fargate-amazon-ecs-aws-graviton2-processors/)
- [AWS Public IPv4 charge](https://repost.aws/knowledge-center/vpc-optimize-ipv4-usage)
- [SSM Parameter Store tiers](https://docs.aws.amazon.com/systems-manager/latest/userguide/parameter-store-advanced-parameters.html)
- [CloudWatch Logs billing](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/LogsBillingDetails.html)
- [東京リージョンARM/x86単価比較（2025年10月）](https://zenn.dev/canly/articles/156386bb65c50e)

> 参照内容はライセンス遵守のため引用せず、見積り条件として要約・再計算した。
