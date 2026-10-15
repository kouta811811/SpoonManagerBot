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
- APIベースURLはアプリ登録リージョンおよびDJアカウントの地域に従う。

## 3. スコープ

### 3.1 MVPに含む
- 単一DJ・単一OAuth同意・単一SSE接続
- OAuth 2.0認可コードフロー、更新、失効
- SSEの接続、フレーム解析、再接続、正常停止
- チャット、入室、ハート、寄付イベントの処理
- `HOST` / `MANAGER` / `LISTENER` の権限制御付きチャットコマンド
- `POST /v1/live/chat` によるBot名義の応答
- 配信中だけ起動するECS Fargateタスク
- DynamoDBによる一時状態、重複抑止、クールダウン
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
- **MANAGER**: 対象配信でHOSTが許可した管理者。
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
3. WHEN OAuth callbackを受信するとき、THE SYSTEM SHALL `state`、有効期限、redirect URIを検証してから認可コードをトークンへ交換する。
4. THE SYSTEM SHALL 1組のアクセストークンを認可した1 DJだけに関連付ける。
5. THE SYSTEM SHALL Client Secret、Access Token、Refresh TokenをSSM Parameter Store Standard TierのSecureStringへ分離保存する。
6. WHEN Access Tokenが期限切れに近い、または期限切れ応答を受けたとき、THE SYSTEM SHALL Refresh Tokenで一度だけ更新し、結果を原子的に保存する。
7. WHEN 同意が拒否、取消、権限不足、または更新不能になったとき、THE SYSTEM SHALL SSE接続とチャット送信を停止し、再認可が必要な状態にする。
8. WHEN DJが連携を解除するとき、THE SYSTEM SHALL 公式失効APIを呼び、保存トークンとOAuth一時状態を削除する。

### R3. Bot起動と停止
1. WHEN OPERATORが配信開始時にBot起動を要求するとき、THE SYSTEM SHALL AWS認証済み操作から1個のECS Fargateタスクを起動する。
2. THE SYSTEM SHALL 同一DJに対する同時実行をDynamoDBのleaseで防止する。
3. WHEN 配信が存在しない、OAuthが無効、権限が不足、または地域が不一致のとき、THE SYSTEM SHALL SSEへ接続せずタスクを終了する。
4. WHEN OPERATORが停止を要求、配信終了を公式に確認、または回復不能エラーを検知したとき、THE SYSTEM SHALL SSE接続を閉じ、leaseを解放してタスクを終了する。
5. WHEN タスクが終了したとき、THE SYSTEM SHALL ECS Serviceの常駐タスクを残さず、コンピュート課金を停止する。

### R4. SSE接続と再接続
1. WHEN Botが起動したとき、THE SYSTEM SHALL 対象DJのBearer Tokenを用いて地域対応の `GET /v1/live/events` へ接続する。
2. THE SYSTEM SHALL HTTPステータスと `Content-Type: text/event-stream` を確認してからSSEデータを解析する。
3. THE SYSTEM SHALL SSE仕様に従い、複数行data、空行区切り、コメント/heartbeat、UTF-8、部分受信を安全に処理する。
4. WHEN 一時的な切断または5xxが発生したとき、THE SYSTEM SHALL 公式再接続規則を優先し、上限付きジッター指数バックオフで再接続する。
5. WHEN 401/403を受けたとき、THE SYSTEM SHALL トークン更新を一度試行し、権限不足または更新失敗なら再接続ループを停止する。
6. WHEN 429を受けたとき、THE SYSTEM SHALL `Retry-After` があれば優先し、接続を連打しない。
7. THE SYSTEM SHALL 公式仕様がイベントID・resumeを提供する場合、最終処理済み位置を保存して再接続に使用する。
8. THE SYSTEM SHALL SSE切断だけを配信終了と判定しない。

### R5. イベント検証と重複抑止
1. WHEN SSEフレームを受信したとき、THE SYSTEM SHALL 公式イベント種別とJSON Schemaを検証してから処理する。
2. WHEN チャット、入室、ハート、寄付イベントを受信したとき、THE SYSTEM SHALL 対応ハンドラーへ振り分ける。
3. WHEN 未知イベントまたは将来追加イベントを受信したとき、THE SYSTEM SHALL 副作用なしで無視し、秘密・活動情報を含めず記録する。
4. WHEN 公式event IDが再受信されたとき、THE SYSTEM SHALL DynamoDBの条件付き書込みで副作用を高々1回に抑える。
5. WHEN 公式event IDが提供されないとき、THE SYSTEM SHALL 公式の順序・再送保証を確認するまで、破壊的な推測IDを導入しない。
6. THE SYSTEM SHALL Bot自身の投稿またはリスナー通知がチャットイベントとして戻る場合、それをコマンドとして再実行しない。

### R6. チャットコマンドとロール制御
1. WHEN チャット本文が `!` または `/` で始まるとき、THE SYSTEM SHALL コマンド名と引数を正規化して解析する。
2. THE SYSTEM SHALL コマンド定義と `HOST` / `MANAGER` / `LISTENER` の許可集合を一元管理する。
3. WHEN ロールを確実に判定できないとき、THE SYSTEM SHALL 最小権限としてLISTENER扱いにする。
4. WHEN 権限不足のコマンドを受けたとき、THE SYSTEM SHALL 実行せず、既定ではチャットを送信しない。
5. THE SYSTEM SHALL `help`、`song`、`dice` および運営コマンドを登録可能にし、正確なコマンド仕様はユーザー提示後に確定する。

### R7. Bot名義チャット送信
1. WHEN 応答が必要なとき、THE SYSTEM SHALL 同じDJ同意のBearer Tokenで `POST /v1/live/chat` を呼び出す。
2. THE SYSTEM SHALL チャットがDJではなくBot Account名義で表示されることを運用者と利用者へ明示する。
3. THE SYSTEM SHALL 公式の本文長、文字、頻度、権限、リクエスト形式を検証してから送信する。
4. THE SYSTEM SHALL 同一コマンドの既定5秒クールダウンをDynamoDBで原子的に判定する。ただし公式制限が厳しい場合は公式値を優先する。
5. WHEN 429を受けたとき、THE SYSTEM SHALL `Retry-After` を優先し、再試行安全性が確認できる場合だけ限定再試行する。
6. WHEN 送信に失敗したとき、THE SYSTEM SHALL 重要なセッション状態を巻き戻さず、best-effortとして記録する。

### R8. イベント別処理
1. WHEN 入室イベントを受信したとき、THE SYSTEM SHALL 設定で有効な場合だけ頻度制限付きウェルカム応答を生成する。
2. WHEN チャットイベントを受信したとき、THE SYSTEM SHALL R6に従ってコマンドを処理する。
3. WHEN ハートイベントを受信したとき、THE SYSTEM SHALL 承認済み仕様に従い配信内集計または応答を行う。
4. WHEN 寄付イベントを受信したとき、THE SYSTEM SHALL 承認済み仕様に従いお礼応答と配信内集計を行う。
5. THE SYSTEM SHALL 公式仕様で確認できない配信終了イベントを仮定せず、現在配信APIまたは正式イベントで終了を確認する。

### R9. リスナー通知
1. WHEN SSEへ接続するとき、THE SYSTEM SHALL SpoonがBot Account名義で投稿するリスナー通知を許容する。
2. THE SYSTEM SHALL 通知を無効化、削除、遮断、改変、または見えにくくしない。
3. THE SYSTEM SHALL 再接続による通知再投稿の挙動を公式仕様で確認し、不要な再接続を抑える。
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
| 配信なし | Fargate Taskを起動しない、または速やかに終了 |
| SSE接続中 | イベント処理、listener noticeを許容 |
| SSE一時切断/5xx | 上限付き再接続 |
| SSE 401 | 一度だけrefresh後に再接続 |
| SSE 403 | 権限/地域を記録して停止 |
| SSE 429 | Retry-After後に再接続 |
| Chat API失敗 | 状態を維持しbest-effortで継続 |
| 配信終了/手動停止 | 接続を閉じTask終了、TTL/削除処理 |

## 7. 非機能要件

### NFR1. 信頼性
- Fargate Task稼働中はSSE接続を監視し、一時切断から自動回復する。
- 切断中に失われるイベントの回復可否は公式resume仕様に従う。提供されない場合は「欠落し得る」ことを明示する。
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

### P0
1. OAuth認可/token/refresh/revoke URL、redirect URI規則、PKCE対応、token TTL。
2. 11権限の正式名称と、チャット・入室・ハート・寄付・チャット送信に必要な最小組合せ。
3. 日本向けSpoon base URLと、アプリ/Bot/DJの地域一致規則。
4. `GET /v1/live/events` の認証ヘッダー、SSE event名、全payload schema、event ID、heartbeat、最大接続時間。
5. SSE再接続、resume/`Last-Event-ID`、順序、重複、同時接続、429規則。
6. 公式にいう5イベントの全種別と、配信終了イベントの有無。
7. `POST /v1/live/chat` のrequest/response、文字数、rate limit、idempotency、SSE echo。
8. Bot自己発言、Bot通知、HOST/MANAGERを識別できるpayload項目。

### ユーザー仕様待ち
1. `help`, `song`, `dice` と管理コマンドの名称、引数、応答、許可ロール。
2. MANAGER登録・解除方法。
3. 入室、ハート、寄付への応答文面・頻度・集計項目。
4. Botの手動開始/停止UIをAWS CLIのままにするか、将来管理画面を追加するか。

## 10. 完了条件
- 公式API仕様との契約テストが合格する。
- OAuth、SSE、再接続、重複、自己発言除外、チャット送信の自動テストが合格する。
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
