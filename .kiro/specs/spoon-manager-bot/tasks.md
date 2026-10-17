# SPOON配信マネージャーBot 実装計画

> Spoon公式のSSE方式へ改訂済み。AWSはdev基盤（`infrastructure/foundation.yaml`）まで。アプリはお礼機能（R13）とその土台（イベントモデル、チャット送信、送信キュー）を実装済みで、SSE接続・OAuth・Workerは未実装。フェーズ0完了前に本番SSEへ接続しない。

## フェーズ0: 公式契約の確定

- [ ] 0.1 Developer App/Bot Accountを準備する
  - DJとは別のBot Accountを作成し、命名規則に適合するDeveloper Appを登録する。
  - app/Bot/DJの地域と日本向けbase URLを確認する。
  - 対応: R1, R9

- [x] 0.2 OAuth契約を記録する
  - token/revoke endpoint、Basic認証（Basic優先・UUIDのclient_id）、code 60秒・二重使用で全token失効、access token 1時間、refresh token 30日・ローテーションあり、tokenレスポンス（snake_case・scope）、PKCE未対応・state最大512B、同意拒否callback（`access_denied`/`invalid_scope`）、RFC 6749エラーを `docs/spoon-api-contract.md` に記録した。
  - 対応: R2

- [x] 0.3 最小権限セットを確定する
  - 権限一覧9種類を `docs/spoon-api-contract.md` に記録した。
  - MVPは `events.chat events.like events.donation chat.send`（コマンドとハート・スプーンのお礼）。入場ウェルカムは `events.presence dj.follow` を追加同意する。
  - [ ] 残り: Developer Appへの登録値とDJ同意要求を一致させる（0.1と合わせて実施）。
  - 対応: R1, R4, R7

- [x] 0.4 SSE契約を記録する
  - headers、status、5イベントのschema、`id:` 形式、15秒heartbeat、`end.reason`、再開未提供、重複接続、入場の扱い、エラーbodyのSSE形式を `docs/spoon-api-contract.md` に記録した。
  - 対応: R4, R5, R8

- [x] 0.5 Chat API契約を記録する
  - request `{"message": string}`（200文字・UTF-16 code unit基準）、成功204ボディなし、空文字400、エラーdetailCode、呼び出し上限とヘッダー、Bot自己発言が戻らないこと、冪等キーなし（再送は最大1回）を `docs/spoon-api-contract.md` に記録した。
  - 対応: R5, R7

- [ ] 0.6 ユーザー機能仕様を承認する
  - [x] ハート・スプーンのお礼を確定した（要件R13、設計§6.3、ADR-013）。DJがチャットコマンドで文面を登録、未登録なら送らない、FREEハートも対象、Bot全体で15秒に1通、複数人まとめ送信。
  - [ ] 残り: command catalog（`help` / `song` / `dice` 等）、MANAGER登録コマンドの書式、入場ウェルカムの文面・頻度。
  - [ ] 残り: ADR-010〜012（MANAGER方式、ウェルカム任意化、配信待ち10分）を承認する。
  - 対応: R6, R8, R13

- [ ] 0.7 コスト見積りと運用手順を承認する
  - 東京regionのFargate 0.25vCPU/0.5GB、public IPv4、ログ等を配信時間別に見積る。
  - AWS Budgets、起動/停止責任者、異常終了時対応を確定する。
  - 対応: R3, R11, R12, NFR4

## フェーズ1: プロジェクト・IaC基盤

- [ ] 1.1 Python 3.12プロジェクトを作成する
  - [x] `pyproject.toml`（src レイアウト、ruff・mypy strict・pytest）、domain / thanks / chat / spoon / bot / observability の分離。
  - [ ] 残り: Lambda entrypoint、Worker、設定の読み込み。
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
  - [x] ruff、mypy、pytest のジョブを追加した。
  - [ ] 残り: SAM validate、container/secret/dependency scan。
  - main反映だけではFargate Taskを起動しない。
  - 対応: R12, NFR5

## フェーズ2: OAuth・地域・秘密管理

- [ ] 2.1 OAuth開始スクリプトとcallbackを実装する
  - state一回利用（最大512B）、TTL、error callback（`access_denied`/`invalid_scope`）を実装する。PKCEは公式未対応のため実装しない。
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

- [ ] 4.3 接続ループを実装する
  - `end.reason`（LIVE_ENDED / TOKEN_EXPIRED / RECONNECT / DUPLICATE_CONNECTION）と、endなし切断・45秒read timeoutによる遷移を実装する。
  - 404 `0301` の配信待ち（待機上限付き）、401 refresh-once、403のdetailCode有無による分岐、500/502のjitter backoffを実装する。
  - 対応: R3, R4

- [ ] 4.4 SSE parser/reconnectの自動テストを作成する
  - 公式fixture、fragmentation、malformed、oversize、全status、長時間mock streamを検証する。
  - 対応: R4, R5

## フェーズ5: イベント・コマンド・状態

- [x] 5.1 公式event modelとdispatcherを実装する
  - `chat` / `presence` / `like` / `donation` / `end` の型付きモデル、nullable項目、未知field・未知eventの無視を実装した（`domain/events.py`、`bot/router.py`）。presence のウェルカムは5.4で追加する。
  - 対応: R5, R8

- [ ] 5.2 MANAGER登録と無視リストを実装する
  - HOSTの管理コマンドによるMANAGERの登録・解除（DynamoDB）と、他Bot等の無視ユーザーID設定を実装する。
  - 対応: R5, R6, R10

- [ ] 5.3 command registry/RBACを実装する
  - `!`/`/`、引数検証、`isDj` によるHOST判定、MANAGER登録、LISTENERの許可集合を実装する。
  - 対応: R6

- [ ] 5.4 presence/like/donation handlerを実装する
  - 任意のウェルカム（同一配信で一度）、`totalAmount` によるハート集計、`amount` によるスプーン集計（配信全体の `SUMMARY`）を実装する。
  - like/donation を ThanksBuffer へ渡す。
  - 対応: R8, R10

- [x] 5.5 お礼文の登録コマンドを実装する
  - `!setheart` / `!setspoon` / `!delheart` / `!delspoon` / `!msglist` をDJ（`isDj`）だけに許可する。
  - 下限の解釈、150文字・5段の上限、上書き、登録結果の返信、DynamoDB `THANKS#...` の読み書きを実装する。
  - 対応: R13, R10

- [x] 5.6 お礼の送信を実装する
  - ThanksBuffer（メモリ、`user.id` 単位で合算、60秒の期限、特別文は最大5件）と、15秒に1通のThanksSchedulerを実装する。
  - 段の選択、優先順（特別文 → スプーン → ハート）、変数展開（`{名前}` に「さん」付与、`{数}`、`{累計}`）、200文字への収め込みと「ほかN名」、null nickname を実装する。
  - 権限（`chat.send` / `events.like` / `events.donation`）がない種別を無効にする。
  - 対応: R13, R7, R10

- [ ] 5.7 event/command/お礼の自動テストを作成する
  - [x] 公式サンプルpayload、nullable項目、未知event、JSON風message、集計の単位。
  - [x] お礼: DJ以外の無視、下限の解釈、未登録なら送らない、合算、15秒制限、優先順、200文字・「ほかN名」、期限切れ、返信レーン優先、ニックネームがログに残らないこと。
  - [ ] 残り: 全command×role（コマンド一覧の確定後）。
  - 対応: R5, R6, R8, R13

## フェーズ6: Chat API

- [x] 6.1 `POST /v1/live/chat` clientを実装する
  - 地域ホストのallowlist、redirect拒否、`{"message"}` 送信、204成功、status → detailCode の順の分類、`x-ratelimit-*` と `Retry-After` の取得、timeoutを実装した（`spoon/chat_client.py`）。
  - 対応: R7

- [ ] 6.2 送信キューとレート制御を実装する
  - [x] 返信レーン（優先）とお礼レーンの2レーン、お礼は失敗しても再送しない、1日上限（`Retry-After`）での送信停止（メモリ上）を実装した（`chat/outbox.py`）。
  - [ ] 残り（以下）:
  - 5秒cooldown、`x-ratelimit-*` ヘッダーによる事前の速度調整、`OAPI_MNGR_0302` の限定再送、1日上限（`Retry-After`）による `CHAT_SUSPEND`、403 `0208` の一時停止を実装する。
  - 本文の空チェックと、非BMP文字を2文字と数える200文字判定を実装する。
  - 対応: R7

- [ ] 6.3 Chat APIの自動テストを作成する
  - 200/400（0103/0108/0109）/401/403（なし/0208）/429（0302/秒/日）/500/502/timeout、ログ漏洩なしを検証する。
  - 対応: R7, R11

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
  - 長時間接続、1時間ごとのTOKEN_EXPIRED再接続、切断、Task再起動、region mismatchを検証する。
  - Botのマネージャー指定有無でpresenceが届くかを確認する。
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
| R4 SSE | 0.4, 4.1〜4.4 |
| R5 イベント検証 | 0.4, 0.5, 4.2, 5.1, 5.2, 5.7 |
| R6 Command/RBAC | 0.6, 5.3, 5.7 |
| R7 Chat | 0.3, 0.5, 5.6, 6.1〜6.3 |
| R8 Event処理 | 0.4, 0.6, 5.1, 5.4, 5.7 |
| R9 Listener notice | 0.1, 7.4 |
| R10 Data | 3.2, 5.2, 5.4〜5.6, 7.1, 7.3 |
| R11 Observability | 0.7, 3.2, 6.3, 7.1〜7.3 |
| R12 Deploy/approval | 0.7, 1.3, 1.4, 8.1, 8.4, 8.5 |
| R13 お礼メッセージ | 0.6, 5.5〜5.7, 6.2 |

## 実装開始ゲート
- [ ] フェーズ0の公式契約・ユーザー仕様・コスト見積りが完了している。
- [x] 公式5 eventとMVPに必要なpermission名（`events.chat` / `chat.send`）が確定している。
- [x] SSEの再接続方針とChat API・呼び出し上限の規則が確定している。
- [ ] refresh tokenの扱いとChat API request schemaが確定している（スケルトン実装は先行可）。
- [ ] Bot AccountとDeveloper Appが準備されている。
- [ ] ユーザーが改訂requirements/design/tasksを承認している。
