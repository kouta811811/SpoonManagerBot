---
inclusion: always
---

# SpoonManagerBot アーキテクチャ方針

- Spoon連携認証はOAuth 2.0認可コードフローを必須とし、固定共有APIトークンで代替しない。
- MVPは単一HOSTを対象とする。複数HOST対応は別specで再設計する。
- OAuthクライアント情報とトークンはSSM Parameter Store Standard TierのSecureStringへ分離保存する。
- Spoon公式仕様が未提供のイベント名、署名、URL、スコープ、レート制限、コマンド詳細は推測で固定せず、公式資料またはユーザー提示仕様を待つ。
- AWS構成はAPI Gateway HTTP API、単一Lambda、単一DynamoDB On-Demandテーブル、SSM Standard Tier、AWS SAMを基本とする。
- MVPではNAT Gateway、常時稼働サーバー、Secrets Manager、SQS、カスタマー管理KMS鍵、有料カスタムメトリクスを追加しない。
- 冪等性はDynamoDB上の `PROCESSING/SUCCEEDED/RETRYABLE_FAILED` とleaseで管理する。
- Spoon API送信はLambda内の同期・best-effortとし、重要な状態更新を先に永続化する。
- 固定費を避け、AWS無料利用枠内を目標とする。ただし月額0円は保証せず、予算通知と利用量確認を行う。
- Spoonの審査・運用承認を得るまで実利用者向け本番公開を行わない。
