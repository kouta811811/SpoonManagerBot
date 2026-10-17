# SPOON配信マネージャーBot 要件定義

## 1. 概要

Spoon Developers公式Open APIと連携し、DJのライブ配信へ接続して、チャットコマンド、入室案内、ハート、寄付への応答を支援するマネージャーBotを構築する。

公式仕様の公開により、イベント受信方式はWebhookではなく `GET /v1/live/events` のServer-Sent Events（SSE）であることが判明した。本書は旧Webhook要件を撤回し、BotからSpoonへ長時間接続する方式へ改訂する。

## 2. 確定した外部仕様

- イベントは `GET /v1/live/events` のSSEで受信する。
- 1本のストリームでチャット、入室、ハート、寄付を受信する。
- DJが承認した権限に対応するイベントだけが流れる。
- チャットは `POST /v1/live/chat` で送信され、登録したBotアカウント名義で表示される。
- OAuthアクセストークンはDJの同意に紐づき、1トークンは1 DJを表す。URLやクエリで別DJを指定しない。
- アプリ登録にはDJ本人のアカウントとは別のSpoon Botアカウントを使用する。
- SSE接続中は、Botが受信する情報についてリスナー向け通知がBot名義で投稿される。この通知は無効化できない。
- APIベースURLはアプリ登録リージョンおよびDJアカウントの地域に従う。日本は `https://jp-openapi.spooncast.net`。
- イベントは `chat` / `presence` / `like` / `donation` / `end` の5種類。`end.reason` で配信終了・token失効・再接続指示・重複接続を区別する。
- access tokenは発行から1時間で失効し、その時刻にストリームも `TOKEN_EXPIRED` で閉じる。
- ストリームの再開（`Last-Event-ID`）は未提供。切断中のイベントは受け取れない。
- Bot自身の発言はストリームに戻らない。`chat` には発言者がマネージャーかどうかの情報がない。
- `presence` はBotがDJのマネージャーに指定されている場合だけ届く。
- エラーはRFC 7807形式の `detailCode` で分岐し、呼び出し上限は秒あたり（連携ごと）と1日あたり（アプリ合計・権限ごと）の2種類。

詳細と確度は [`docs/spoon-api-contract.md`](../../../docs/spoon-api-contract.md) を正とする。

## 3. スコープ

### 3.1 MVPに含む
- 単一DJ・単一OAuth同意・単一SSE接続
- OAuth 2.0認可コードフロー、更新、失効
- SSEの接続、フレーム解析、再接続、正常停止
- チャット、入室、ハート、寄付イベントの処理
- `HOST` / `MANAGER` / `LISTENER` の権限制御付きチャットコマンド
- `POST /v1/live/chat` によるBot名義の応答
- 配信中だけ起動するECS Fargateタスク
- DynamoDBによる起動lease、配信内集計、MANAGER登録、クールダウン
- 入場ウェルカム（任意機能。Botのマネージャー指定が必要）
- SSM SecureStringによる秘密・トークン管理
- AWS SAM/CloudFormationとGitHub Actions OIDCによるデプロイ

### 3.2 MVPに含まない
- 複数DJ・複数同時配信
- Webhook受信、Webhook署名検証、API Gateway経由のイベント受信
- 未承認API、イベント、権限の利用
- リスナーの配信横断追跡、プロファイリング、識別子の復号・照合
- 活動情報を用いたAI/機械学習の学習・評価
- Spoonの審査・運用承認前の一般公開または実配信運用

## 4. 利用主体

- **Developer App**: Spoon Developersへ登録するアプリケーション。
- **Bot Account**: Developer Appを登録し、チャットとリスナー通知の表示名となるDJとは別のSpoonアカウント。
- **DJ / HOST**: OAuth 2.0で自身のライブ配信に必要な権限を委任する配信者。
- **MANAGER**: HOSTがBotの設定（管理コマンド）で登録した管理者。Spoonアプリ上のマネージャー指定とは独立して管理する。
- **LISTENER**: 対象配信の一般参加者。
- **OPERATOR**: AWS上のBotをデプロイし、配信開始・停止、監視、事故対応を行う者。

## 5. 機能要件

### R1. アプリ登録と地域設定
1. THE SYSTEM SHALL DJ本人とは別のBot AccountでDeveloper Appを登録する。
2. THE SYSTEM SHALL Spoon公式の命名規則に従い、公式サービスと誤認される名称を使用しない。
3. THE SYSTEM SHALL Developer Appに登録した権限のうち、MVP機能に必要な最小権限だけをOAuth同意画面で要求する。
4. THE SYSTEM SHALL アプリ登録リージョンとDJアカウント地域に適合する公式APIベースURLを設定から選択する。
5. WHEN 地域設定が一致しない、または許可リスト外のAPIホストへ解決されるとき、THE SYSTEM SHALL 接続せず安全に失敗する。

### R2. OAuth 2.0認可
1. THE SYSTEM SHALL OAuth 2.0認可コードフローを使用し、固定共有APIトークンで代替しない。
2. WHEN DJが連携を開始するとき、THE SYSTEM SHALL 推測困難で一回限りの `state` を発行し、公式仕様が対応する場合はPKCEを使用する。
3. WHEN OAuth callbackを受信するとき、THE SYSTEM SHALL `state`、有効期限、redirect URIを検証し、受信から60秒以内に認可コードを `POST /v1/oauth/token` でトークンへ交換する。
3a. THE SYSTEM SHALL OAuth endpointへはBasic認証と `application/x-www-form-urlencoded` で送信し、エラーはRFC 6749の `error` で分岐する。
4. THE SYSTEM SHALL 1組のアクセストークンを認可した1 DJだけに関連付ける。
5. THE SYSTEM SHALL Client Secret、Access Token、Refresh TokenをSSM Parameter Store Standard TierのSecureStringへ分離保存する。
6. WHEN `end.reason=TOKEN_EXPIRED`、401、またはAccess Tokenの期限（発行から1時間）が近いとき、THE SYSTEM SHALL Refresh Tokenで一度だけ更新し、結果を原子的に保存する。refresh後も401なら再試行せず停止する。
7. WHEN 同意が拒否、取消、権限不足、または更新不能になったとき、THE SYSTEM SHALL SSE接続とチャット送信を停止し、再認可が必要な状態にする。
8. WHEN DJが連携を解除するとき、THE SYSTEM SHALL 公式失効APIを呼び、保存トークンとOAuth一時状態を削除する。

### R3. Bot起動と停止
1. WHEN OPERATORが配信開始時にBot起動を要求するとき、THE SYSTEM SHALL AWS認証済み操作から1個のECS Fargateタスクを起動する。
2. THE SYSTEM SHALL 同一DJに対する同時実行をDynamoDBのleaseで防止する。
3. WHEN OAuthが無効、権限が不足、または地域が不一致のとき、THE SYSTEM SHALL SSEへ接続せずタスクを終了する。
3a. WHEN 起動時のSSE接続が404（`OAPI_MNGR_0301`、配信中でない）のとき、THE SYSTEM SHALL 数秒以上の間隔で再試行し、設定した待機上限（既定10分）を超えたらタスクを終了する。
4. WHEN OPERATORが停止を要求、`end.reason=LIVE_ENDED` または `DUPLICATE_CONNECTION` を受信、または回復不能エラーを検知したとき、THE SYSTEM SHALL SSE接続を閉じ、leaseを解放してタスクを終了する。
5. WHEN タスクが終了したとき、THE SYSTEM SHALL ECS Serviceの常駐タスクを残さず、コンピュート課金を停止する。

### R4. SSE接続と再接続
1. WHEN Botが起動したとき、THE SYSTEM SHALL 対象DJのBearer Tokenと `Accept: text/event-stream` で地域対応の `GET /v1/live/events` へ接続する。配信中かどうかを事前に別APIで確認しない。
2. THE SYSTEM SHALL HTTPステータスで先に分岐する。エラーbodyもSSE形式（`data:` で始まる）のため、bodyが必要な場合だけ `data:` を外してparseする。
3. THE SYSTEM SHALL SSE仕様に従い、`event:` / `data:` / `id:`、空行区切り、`:` で始まるcomment（15秒ごとのheartbeat）、UTF-8、部分受信を安全に処理する。`data:` のないフレームは読み飛ばす。
4. THE SYSTEM SHALL `end.reason` に応じて次の動作を選ぶ。`LIVE_ENDED` は終了、`TOKEN_EXPIRED` はrefresh後すぐ再接続、`RECONNECT` はバックオフなしですぐ再接続、`DUPLICATE_CONNECTION` は再接続せず終了。
5. WHEN `end` なしで切断された、または502/500を受けたとき、THE SYSTEM SHALL 1秒から倍増し最大30秒のジッター付き指数バックオフで再接続する。
6. WHEN heartbeatを含む受信が45秒以上途絶えたとき、THE SYSTEM SHALL 接続を切断とみなして再接続する。
7. WHEN 401を受けたとき、THE SYSTEM SHALL refreshを一度だけ試し、それでも401なら停止して再連携を要求する。
8. WHEN detailCodeなしの403を受けたとき、THE SYSTEM SHALL 権限不足として停止し再同意を要求する。`OAPI_MNGR_0209` のときは、DJがブロックを解除する必要があるとして停止する。401と403を同じ処理にしない。
9. THE SYSTEM SHALL ストリーム終了だけを配信終了と判定せず、`end.reason` で判断する。
10. THE SYSTEM SHALL 同じDJへのストリームを同時に1本だけ維持する。

### R5. イベント検証
1. WHEN SSEフレームを受信したとき、THE SYSTEM SHALL `chat` / `presence` / `like` / `donation` / `end` の公式schemaで検証してから処理する。nullableな項目（`nickname`、`combo`、`fanRank`、`favoriteTemperature`、`donation.message`）のnullを許容する。
2. THE SYSTEM SHALL 未知のフィールドを無視し、未知のイベント種別は副作用なしで無視する。
3. THE SYSTEM SHALL チャットの `message` がJSONに見えても文字列として扱い、parseしない。
4. THE SYSTEM SHALL 時刻をISO-8601として解析し、小数部の桁数を固定しない。
5. THE SYSTEM SHALL リスナーの識別に `user.id` だけを使い、`nickname` を識別に使わない。
6. THE SYSTEM SHALL ストリームの再開（`Last-Event-ID`）を前提にしない。切断中のイベントは欠落し得るものとして扱う。
7. THE SYSTEM SHALL 同じ配信にいる他のBotの発言にコマンドとして応答し続けるループを防ぐため、Bot由来と判定した発言者（設定したユーザーID）を無視できる。

### R6. チャットコマンドとロール制御
1. WHEN チャット本文が `!` または `/` で始まるとき、THE SYSTEM SHALL コマンド名と引数を正規化して解析する。
2. THE SYSTEM SHALL コマンド定義と `HOST` / `MANAGER` / `LISTENER` の許可集合を一元管理する。
2a. THE SYSTEM SHALL `chat.isDj=true` の発言者をHOSTと判定する。
2b. THE SYSTEM SHALL HOSTがBotの管理コマンドで登録・解除した `user.id` をMANAGERと判定する。`user.id` はBotとDJの組合せで不変のため、登録はHOSTが解除するか連携解除されるまでDynamoDBに保持する（保有期間はSpoon審査で申告する）。
3. WHEN ロールを確実に判定できないとき、THE SYSTEM SHALL 最小権限としてLISTENER扱いにする。
4. WHEN 権限不足のコマンドを受けたとき、THE SYSTEM SHALL 実行せず、既定ではチャットを送信しない。
5. THE SYSTEM SHALL `help`、`song`、`dice` および運営コマンドを登録可能にし、正確なコマンド仕様はユーザー提示後に確定する。

### R7. Bot名義チャット送信
1. WHEN 応答が必要なとき、THE SYSTEM SHALL 同じDJ同意のBearer Tokenで `POST /v1/live/chat` を呼び出す。
2. THE SYSTEM SHALL チャットがDJではなくBot Account名義で表示されることを運用者と利用者へ明示する。
3. THE SYSTEM SHALL 送信前に本文が空・空白のみでないこと、および絵文字を2文字と数える保守的な数え方で200文字以内であることを検証する。超える場合は切り詰めるか送信しない。
4. THE SYSTEM SHALL 同一コマンドの既定5秒クールダウンを判定する。
5. THE SYSTEM SHALL レスポンスの `x-ratelimit-remaining` 等の上限ヘッダーを読み、429になる前に送信速度を落とす。
6. WHEN 429を受けたとき、THE SYSTEM SHALL 次のとおり扱う。`OAPI_MNGR_0302`（配信全体の混雑を含む）は少し待って再送、detailCodeなし・`Retry-After` なしは指数バックオフ、`Retry-After` ありは1日上限としてその秒数までチャット送信を止める（SSE受信は継続）。
7. WHEN 403 `OAPI_MNGR_0208`（チャット凍結またはBotのチャット禁止）を受けたとき、THE SYSTEM SHALL 少し後に再試行し、続く場合は送信を止めて記録する。
8. WHEN 送信に失敗したとき、THE SYSTEM SHALL 重要なセッション状態を巻き戻さず、best-effortとして記録する。エラーの `message` をそのままチャットへ出さない。

### R8. イベント別処理
1. WHEN `presence`（入場）を受信したとき、THE SYSTEM SHALL 設定で有効な場合だけウェルカム応答を生成する。同じ `user.id` へは同じ配信中に二度あいさつしない。
1a. THE SYSTEM SHALL 入場ウェルカムを任意機能とする。有効化には `events.presence` の同意と、DJによるBotのマネージャー指定（BotがDJを先にフォローしている必要あり）が必要であることを運用手順に記載する。
2. WHEN `chat` を受信したとき、THE SYSTEM SHALL R6に従ってコマンドを処理する。
3. WHEN `like`（ハート）を受信したとき、THE SYSTEM SHALL `totalAmount` で配信内集計する。応答は `type=PAID` だけを既定とし、`FREE` に応答する場合はクールダウンを置く。
4. WHEN `donation`（ギフト）を受信したとき、THE SYSTEM SHALL 設定に従いお礼応答と、`amount`（合計スプーン数、掛け算済み）による配信内集計を行う。`like.amount` と単位を混同しない。
5. THE SYSTEM SHALL 配信終了を `end.reason=LIVE_ENDED` で判定する。

### R9. リスナー通知
1. WHEN SSEへ接続するとき、THE SYSTEM SHALL SpoonがBot Account名義で投稿するリスナー通知を許容する。
2. THE SYSTEM SHALL 通知を無効化、削除、遮断、改変、または見えにくくしない。
3. THE SYSTEM SHALL 接続するとBotがリスナーとして入場すること、および最初の入場から5時間で入場記録が失効し、その後の再接続で入場メッセージが再表示されることを運用者へ明示する。
4. THE SYSTEM SHALL 開発・テスト接続でも実配信へ通知され得るものとして、接続先と開始操作を管理する。

### R10. データ保護
1. THE SYSTEM SHALL 承認された機能に必要な最小限の活動情報だけを処理する。
2. THE SYSTEM SHALL 生チャット、ニックネーム、リスナー識別子をCloudWatch Logsへ出力しない。
3. THE SYSTEM SHALL 一時状態へ既定24時間の論理期限とDynamoDB TTLを設定し、期限切れデータを読み取らない。
4. WHEN 配信、アプリ提供、利用契約が終了したとき、THE SYSTEM SHALL 活動情報を速やかに明示削除できる。
5. THE SYSTEM SHALL 活動情報を目的外利用、販売、無断提供、追跡、AI/ML学習・評価に使用しない。

### R11. 可観測性と事故対応
1. THE SYSTEM SHALL 構造化ログへ接続状態、匿名化DJ/イベントID、イベント種別、結果、遅延、再接続回数を記録する。
2. THE SYSTEM SHALL Token、Secret、Authorization、SSE生データ、チャット全文、ニックネームをログへ出力しない。
3. THE SYSTEM SHALL ECS Task停止、異常終了、認証失敗、権限不足、再接続超過、チャットAPI失敗を検知可能にする。
4. THE SYSTEM SHALL セキュリティ事故時にタスク停止、トークン失効・削除、影響調査、Spoonへの通知を行えるランブックを備える。

### R12. デプロイと変更管理
1. THE SYSTEM SHALL OAuth callback用API Gateway HTTP API + Lambda、制御Lambda、ECS Fargate、DynamoDB、SSM、ログをIaCで定義する。
2. THE SYSTEM SHALL GitHub ActionsからOIDC短期認証でデプロイし、長期AWSキーを保存しない。
3. THE SYSTEM SHALL Lambda/ECS Task/CIロールへ最小権限を付与する。
4. THE SYSTEM SHALL Spoonの審査・運用承認前に本番SSE接続を開始しない。
5. WHEN 主要機能、権限、データ利用・保有期間を変更するとき、THE SYSTEM SHALL 本番適用前に再審査要否を確認する。

## 6. 状態・結果契約

| 状態 | 動作 |
|---|---|
| OAuth未認可/失効 | 起動拒否、再認可要求 |
| 起動時404（配信中でない） | 数秒以上の間隔で再試行、待機上限で終了 |
| SSE接続中 | イベント処理、listener noticeを許容 |
| `end` LIVE_ENDED | 接続を閉じTask終了、TTL/削除処理 |
| `end` TOKEN_EXPIRED（1時間ごと） | refresh後すぐ再接続 |
| `end` RECONNECT | バックオフなしですぐ再接続 |
| `end` DUPLICATE_CONNECTION | 再接続せずTask終了 |
| endなし切断 / 500 / 502 | 1〜30秒のジッター付き指数バックオフで再接続 |
| SSE 401 | refreshを1回、それでも401なら停止・再連携要求 |
| SSE 403（detailCodeなし） | 権限不足として停止・再同意要求 |
| SSE 403 `0209` | 停止、DJにブロック解除を案内 |
| Chat 429 / 403 `0208` / 失敗 | 送信だけ待機・抑止し、SSE受信は継続 |
| 手動停止 | 接続を閉じTask終了 |

## 7. 非機能要件

### NFR1. 信頼性
- Fargate Task稼働中はSSE接続を監視し、一時切断から自動回復する。
- 公式は現在ストリームの再開を提供しないため、切断中のイベントは欠落し得る。`RECONNECT` はすぐ再接続して欠落を最小化する。
- Fargate Spotは中断による接続欠落があるためMVP本番では使用しない。

### NFR2. 性能
- 受信イベントは通常時p95 1秒以内にハンドラーへ引き渡すことを目標とする。
- チャットAPI呼出しは接続・読取タイムアウトを持ち、SSE読取ループを長時間ブロックしない。
- Fargate初期値はLinux/ARM64、0.25 vCPU、0.5GBメモリとし、検証後に最小値を調整する。

### NFR3. セキュリティ
- APIホストは地域別allowlistで固定し、Bearer Tokenを任意URLや別hostへのredirectへ送信しない。
- Fargate Taskはpublic subnetから外向き通信のみ行い、Security Groupのinboundを全拒否する。
- NAT Gateway、ALB、公開受信ポートを設けない。
- 保存時暗号化、最小権限、秘密分離、依存関係・コンテナスキャンを適用する。

### NFR4. コスト
- ECS Serviceを常駐させず、配信中だけFargate Taskを実行する。
- API Gateway HTTP API、Lambda、DynamoDB On-Demand、SSM Standard Tier、短期ログを利用する。
- NAT Gateway、ALB、Secrets Manager、RDS、ElastiCache、Step Functionsを使用しない。
- Fargate、ログ、DynamoDB、API Gateway、およびタスク用public IPv4等の従量料金は発生し得る。月額0円は保証しない。
- デプロイ前見積り、AWS Budgets通知、配信ごとのTask稼働時間記録を必須とする。

### NFR5. 保守性
- SSE parser、OAuth、地域解決、Spoon API、DynamoDB、時刻/待機をインターフェース分離する。
- 公式イベント/API仕様変更を型付きモデルと設定の更新で追従可能にする。

## 8. 確定アーキテクチャ方針
- OAuth callback/control plane: API Gateway HTTP API + Lambda。
- ライブイベント処理: 配信中だけ実行するECS Fargate On-Demand Task。
- 状態: 単一DynamoDB On-Demandテーブル。
- 秘密: SSM Parameter Store Standard SecureString + AWS管理キー。
- イメージ: 公開リポジトリのコンテナをdigest固定し、秘密をイメージへ含めない。
- ネットワーク: public subnet + outbound only。NAT Gateway/ALBなし。

## 9. 実装前に公式詳細を確認する項目

### 確定済み（`docs/spoon-api-contract.md`）
- 日本のbase URL、同意画面URL、token/revoke endpoint、access token有効期限（1時間）、認可コード期限（60秒）
- イベントストリームの認証・5イベントのschema・heartbeat・`end.reason`・再開未提供・重複接続・入場の扱い
- 確認できた権限名（`events.chat` / `events.presence` / `events.like` / `events.donation` / `chat.send` / `live.read`）
- エラー形式とdetailCode、呼び出し上限（秒あたり・1日あたり）とヘッダー
- チャット本文の上限（200文字）、Bot自己発言がストリームに戻らないこと

### P0（未確認、実装前に確認）
1. refresh tokenの有効期限と、refresh時のローテーション有無。
2. PKCE対応有無、同意拒否時のcallbackパラメータ。
3. `POST /v1/live/chat` のrequest/response schemaと再送の安全性。
4. 残り5種類の権限名と、`POST /v1/live/follow` に必要な権限。

### ユーザー仕様待ち
1. `help`, `song`, `dice` と管理コマンドの名称、引数、応答、許可ロール。
2. MANAGER登録・解除コマンドの名称と書式（方式は「HOSTがBotの管理コマンドで登録」で仮決定）。
3. 入室、ハート、寄付への応答文面・頻度・集計項目。
4. Botの手動開始/停止UIをAWS CLIのままにするか、将来管理画面を追加するか。

## 10. 完了条件
- 公式API仕様との契約テストが合格する。
- OAuth、SSE、`end.reason` 別の再接続、エラー分岐、レート制御、チャット送信の自動テストが合格する。
- Fargate Taskが配信時だけ起動し、終了後に停止する。
- IAM、秘密、ログ、データ削除、コスト見積りのレビューが完了する。
- Spoonの審査・認証・運用承認を得るまでは本番接続しない。

## 11. 参照資料
- [Spoon Developer Docs](https://developers.spooncast.net/docs/en)
- [Build a bot](https://developers.spooncast.net/docs/en/getting-started/build-a-bot)
- [Register an app](https://developers.spooncast.net/docs/en/getting-started/app-registration)
- [Get a DJ's consent](https://developers.spooncast.net/docs/en/getting-started/user-authorization)
- [Choose permissions](https://developers.spooncast.net/docs/en/getting-started/choosing-permissions)
- [Live events (SSE)](https://developers.spooncast.net/docs/en/api-reference/live-events)
- [Chat API](https://developers.spooncast.net/docs/en/api-reference/chat)
- [Token API](https://developers.spooncast.net/docs/en/api-reference/tokens)
- [Endpoint overview](https://developers.spooncast.net/docs/en/api-reference/endpoints)

> 参照サイトの内容はライセンス遵守のため引用せず、要件として要約・再構成した。
