---
inclusion: always
---

# SpoonManagerBot アーキテクチャ方針

- 公式ライブイベントはWebhookではなく `GET /v1/live/events` のServer-Sent Events（SSE）で受信する。Webhook署名や `POST /webhook` を実装しない。
- Spoon連携認証はOAuth 2.0認可コードフローを必須とし、固定共有APIトークンで代替しない。
- MVPは単一DJ・単一OAuth token・単一SSE接続とする。1 tokenは認可した1 DJを表す。
- Developer AppはDJ本人とは別のSpoon Botアカウントで登録する。チャットとlistener noticeはBotアカウント名義となる。
- SSE接続中のSpoon listener noticeを無効化、遮断、改変しない。
- OAuth client情報とtokenはSSM Parameter Store Standard TierのSecureStringへ分離保存する。
- Spoon API base URLはapp/DJ地域に合う公式hostのallowlistから選び、任意URLや別host redirectへBearer tokenを送らない。
- OAuth callback/control planeはAPI Gateway HTTP API + Lambda、SSE workerは配信中だけECS Fargate On-Demand Taskで実行する。
- ECS Service、NAT Gateway、ALB、Secrets Manager、RDS、ElastiCache、Fargate SpotをMVPで使用しない。
- 起動lease、MANAGER登録、cooldown、チャット送信停止、配信内集計は単一DynamoDB On-Demandテーブルで管理する。ストリーム再開は未提供のためcheckpoint・イベント重複抑止テーブルは持たない。
- Spoonチャットは `POST /v1/live/chat` で同期best-effort送信し、重要状態を先に永続化する。
- 外部仕様は `docs/spoon-api-contract.md` を正とする。そこに「未確認」とある項目（refresh tokenの扱い、PKCE、Chat request schema）とcommand詳細を推測で固定しない。
- 要求するscopeは使う機能の分だけにする。MVPは `events.chat events.like events.donation chat.send`（コマンドとハート・スプーンのお礼）。
- ハート・スプーンのお礼文はDJがチャットコマンドで登録したものだけを送る（未登録なら送らない）。お礼はBot全体で15秒に1通とし、送り主の情報はメモリだけに置く。
- 接続ループは `end.reason` で分岐する。401と403を同じ扱いにせず、401は無限に再試行しない。エラーは `detailCode` で分岐し、`message` を分岐や表示に使わない。
- 月額0円を保証しない。Fargate Taskを配信中だけ起動し、停止後のcompute課金を避け、予算通知と稼働時間記録を行う。
- Spoonの審査・運用承認を得るまで実利用者向けprod接続を行わない。
