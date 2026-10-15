# SPOON配信マネージャーBot 実装計画

> Spoon公式のSSE方式へ改訂済み。現時点ではspec更新のみで、アプリコード・AWSリソースは未実装。フェーズ0完了前に本番SSEへ接続しない。

## フェーズ0: 公式契約の確定

- [ ] 0.1 Developer App/Bot Accountを準備する
  - DJとは別のBot Accountを作成し、命名規則に適合するDeveloper Appを登録する。
  - app/Bot/DJの地域と日本向けbase URLを確認する。
  - 対応: R1, R9

- [ ] 0.2 OAuth契約を記録する
  - authorization/token/refresh/revoke URL、redirect規則、PKCE、TTL、エラーを記録する。
  - 1 token = 1 DJをfixtureと契約テストへ反映する。
  - 対応: R2

- [ ] 0.3 最小権限セットを確定する
  - 11権限からchat/join/heart/donation受信とchat送信に必要なものだけを選ぶ。
  - Developer App登録値とDJ同意要求を一致させる。
  - 対応: R1, R4, R7

- [ ] 0.4 SSE契約を記録する
  - `GET /v1/live/events` のheaders、status、event名、5 payload、event ID、heartbeat、接続上限を記録する。
  - reconnect、retry、resume、順序、重複、同時接続、live endの規則を確認する。
  - error bodyのSSE形式もfixture化する。
  - 対応: R4, R5, R8

- [ ] 0.5 Chat API契約を記録する
  - `POST /v1/live/chat` のschema、文字制限、rate limit、Retry-After、再試行安全性を確認する。
  - Bot投稿のSSE echoと自己識別方法を確認する。
  - 対応: R5, R7

- [ ] 0.6 ユーザー機能仕様を承認する
  - command catalog、ロール、MANAGER設定、welcome/heart/donation応答を確定する。
  - 対応: R6, R8

- [ ] 0.7 コスト見積りと運用手順を承認する
  - 東京regionのFargate 0.25vCPU/0.5GB、public IPv4、ログ等を配信時間別に見積る。
  - AWS Budgets、起動/停止責任者、異常終了時対応を確定する。
  - 対応: R3, R11, R12, NFR4

## フェーズ1: プロジェクト・IaC基盤

- [ ] 1.1 Python 3.12プロジェクトを作成する
  - Lambda entrypoint、Worker、domain、Spoon adapters、repositoriesを分離する。
  - 対応: NFR5

- [ ] 1.2 コンテナを作成する
  - Linux/ARM64、non-root、read-only filesystem、digest固定可能なimageを作る。
  - credentialをimageへ含めない。
  - 対応: R3, NFR3

- [ ] 1.3 SAM/CloudFormationを作成する
  - OAuth callback HTTP API/Lambda、Control Lambda、ECS cluster/task definition、public subnet/IGW、DynamoDB、SSM権限、7日ログを定義する。
  - ECS Service、NAT Gateway、ALB、inbound ruleを作らない。
  - 対応: R2, R3, R12, NFR3, NFR4

- [ ] 1.4 CIを作成する
  - lint、type、pytest、SAM validate、container/secret/dependency scanを実行する。
  - main反映だけではFargate Taskを起動しない。
  - 対応: R12, NFR5

## フェーズ2: OAuth・地域・秘密管理

- [ ] 2.1 OAuth開始スクリプトとcallbackを実装する
  - state一回利用、TTL、対応時PKCE、error callbackを実装する。
  - 対応: R2

- [ ] 2.2 Token Storeを実装する
  - SSM Standard SecureStringへclient/access/refresh tokenを分離保存する。
  - refresh lock、更新、revoke、削除を実装する。
  - 対応: R2, R10

- [ ] 2.3 Spoon Region Resolverを実装する
  - 公式base URL表、app/DJ region検証、host allowlist、redirect拒否を実装する。
  - 対応: R1, NFR3

- [ ] 2.4 OAuth/regionの自動テストを作成する
  - invalid/reused/expired state、refresh競合、revoke、region mismatch、host injectionを検証する。
  - 対応: R1, R2

## フェーズ3: 起動制御とFargate Worker

- [ ] 3.1 Control Lambdaを実装する
  - IAM認証済みstart/stop、DynamoDB lease、RunTask/StopTaskを実装する。
  - 対応: R3

- [ ] 3.2 Worker lifecycleを実装する
  - 起動検証、lease heartbeat、graceful shutdown、exit code、TTL処理を実装する。
  - 対応: R3, R10, R11

- [ ] 3.3 lifecycleの自動テストを作成する
  - 二重起動、stale lease、配信なし、stop、異常終了、権限不足を検証する。
  - 対応: R3

## フェーズ4: SSEクライアント

- [ ] 4.1 streaming HTTP clientを実装する
  - `GET /v1/live/events`、Bearer/Accept header、status/content-type、timeoutを実装する。
  - 対応: R4

- [ ] 4.2 SSE parserを実装する
  - chunk境界、CRLF/LF、複数data、id/event/retry、comment、heartbeat、上限を実装する。
  - 対応: R4, R5

- [ ] 4.3 再接続状態機械を実装する
  - 401 refresh-once、403 stop、429 Retry-After、5xx/切断full-jitter、resumeを実装する。
  - 対応: R4

- [ ] 4.4 SSE parser/reconnectの自動テストを作成する
  - 公式fixture、fragmentation、malformed、oversize、全status、長時間mock streamを検証する。
  - 対応: R4, R5

## フェーズ5: イベント・コマンド・状態

- [ ] 5.1 公式event modelとdispatcherを実装する
  - chat/join/heart/donationと5番目のevent、未知eventを実装する。
  - 対応: R5, R8

- [ ] 5.2 重複抑止/checkpointを実装する
  - 公式event ID/resume保証に従ってDynamoDB条件付き処理を行う。
  - 対応: R4, R5, R10

- [ ] 5.3 自己投稿除外を実装する
  - Bot chatとlistener noticeがstreamへ戻ってもcommand loopを起こさない。
  - 対応: R5, R9

- [ ] 5.4 command registry/RBACを実装する
  - `!`/`/`、引数検証、HOST/MANAGER/LISTENER許可集合を実装する。
  - 対応: R6

- [ ] 5.5 join/heart/donation handlerを実装する
  - 承認済み頻度、文面、非個人別集計を実装する。
  - 対応: R8, R10

- [ ] 5.6 event/commandの自動テストを作成する
  - 公式event fixture、未知event、重複、自己投稿、全command×roleを検証する。
  - 対応: R5, R6, R8

## フェーズ6: Chat API

- [ ] 6.1 `POST /v1/live/chat` clientを実装する
  - 同一DJ token/region、schema、timeout、応答検証を実装する。
  - 対応: R7

- [ ] 6.2 cooldown/rate limitを実装する
  - DynamoDBの原子的5秒cooldownと公式limitを適用する。
  - 429/Retry-Afterと安全な場合だけの限定再試行を実装する。
  - 対応: R7

- [ ] 6.3 Chat APIの自動テストを作成する
  - 200/400/401/403/429/5xx/timeout、自己echo、ログ漏洩なしを検証する。
  - 対応: R5, R7, R11

## フェーズ7: 可観測性・データ保護

- [ ] 7.1 構造化ログを実装する
  - connection state、匿名ID、event/result/reconnect/errorをallowlist出力する。
  - raw SSE/chat/nickname/token/secretが出ないことをテストする。
  - 対応: R10, R11

- [ ] 7.2 標準監視と予算通知を実装する
  - ECS stopped、Lambda/DynamoDB error、lease heartbeat、ログベース調査、AWS Budgetsを設定する。
  - 対応: R11, NFR4

- [ ] 7.3 明示削除と事故対応runbookを作成する
  - Task停止、token revoke/delete、session delete、Spoon通知を手順化する。
  - 対応: R2, R10, R11

- [ ] 7.4 listener notice適合性を確認する
  - 通知を妨げず、接続/再接続/停止時の表示を実配信前に確認する。
  - 対応: R9

## フェーズ8: デプロイ・受入

- [ ] 8.1 GitHub Actions OIDCデプロイを実装する
  - branch/environment条件付きtrustと最小権限を設定する。
  - 対応: R12

- [ ] 8.2 dev統合試験を実施する
  - OAuth→RunTask→SSE→event→chat→停止を検証する。
  - 長時間接続、切断、resume、Task再起動、region mismatchを検証する。
  - 対応: R1〜R12

- [ ] 8.3 性能・コスト試験を実施する
  - event burst、メモリ、CPU、p95、Task稼働時間、ログ量、実コストを測定する。
  - 対応: NFR1〜NFR5

- [ ] 8.4 セキュリティ・規約レビューを完了する
  - IAM、token、region allowlist、container、ログ、TTL、削除、listener noticeを確認する。
  - 対応: R1, R2, R9〜R12

- [ ] 8.5 Spoonの審査・運用承認後にprodを有効化する
  - 承認前はprod Taskを起動しない。
  - 対応: R12

## 要件カバレッジ

| 要件 | 主なタスク |
|---|---|
| R1 登録・地域 | 0.1, 0.3, 2.3, 2.4 |
| R2 OAuth | 0.2, 2.1, 2.2, 2.4, 7.3 |
| R3 起動・停止 | 0.7, 1.3, 3.1〜3.3 |
| R4 SSE | 0.4, 4.1〜4.4, 5.2 |
| R5 検証・重複 | 0.4, 0.5, 4.2, 5.1〜5.3, 5.6 |
| R6 Command/RBAC | 0.6, 5.4, 5.6 |
| R7 Chat | 0.3, 0.5, 6.1〜6.3 |
| R8 Event処理 | 0.4, 0.6, 5.1, 5.5, 5.6 |
| R9 Listener notice | 0.1, 5.3, 7.4 |
| R10 Data | 3.2, 5.2, 5.5, 7.1, 7.3 |
| R11 Observability | 0.7, 3.2, 6.3, 7.1〜7.3 |
| R12 Deploy/approval | 0.7, 1.3, 1.4, 8.1, 8.4, 8.5 |

## 実装開始ゲート
- [ ] フェーズ0の公式契約・ユーザー仕様・コスト見積りが完了している。
- [ ] 公式5 eventと必要permissionの正確な名称が確定している。
- [ ] SSE reconnect/resumeとChat API rate limitが確定している。
- [ ] Bot AccountとDeveloper Appが準備されている。
- [ ] ユーザーが改訂requirements/design/tasksを承認している。
