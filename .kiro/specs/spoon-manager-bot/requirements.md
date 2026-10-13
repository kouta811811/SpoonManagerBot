# SPOON配信マネージャーBot 要件定義

## 1. 概要

Spoon Developers/Open APIと連携し、Spoon Live配信における入室案内、コメントコマンド、ギフト応答、配信セッション終了処理を支援するマネージャーBotを構築する。

本書は `input/` 配下の方式設計書、詳細設計書、構成図、Spoon Developers利用規約を実装可能な要求へ整理したものである。実装開始前に「11. 未確定事項」を解消し、Spoon公式APIドキュメントおよび審査条件との整合を確認すること。

## 2. スコープ

### 2.1 MVPに含む
- `POST /webhook` によるWebhook受信
- Webhook署名検証
- ペイロード検証とイベント重複排除
- 入室、コメント、ギフト、配信終了イベントの振り分け
- `HOST` / `MANAGER` / `LISTENER` の権限制御付きコメントコマンド
- Spoon Open APIを介したチャット応答
- 配信セッション、イベント、クールダウン等の一時状態管理
- 構造化ログ、監視、PII・秘密情報の保護
- AWS SAMおよびGitHub Actions OIDCによるデプロイ基盤

### 2.2 MVPに含まない
- Spoonの非公開・未承認APIへの接続
- リスナーのプロファイリング、配信横断追跡、識別子の復号・照合
- 活動情報を用いたAI/機械学習の学習・評価
- 課金、広告、分析ダッシュボード
- Spoon以外の配信プラットフォーム対応
- Spoonから承認されていない状態での一般公開または実配信運用

## 3. 利用者とロール
- **HOST**: OAuth 2.0でBotへ必要権限を委任した配信者。全ての承認済みコマンドを利用できる。
- **MANAGER**: 対象配信でHOSTが事前に指定した管理者。運営管理コマンドと照会コマンドを利用できる。
- **LISTENER**: 対象配信の一般参加者。照会・参加系の許可コマンドのみ利用できる。
- **OPERATOR**: Botのデプロイ、設定、監視、事故対応を行う開発・運用者。

## 4. 機能要件

### R1. Webhook受信
**ユーザーストーリー:** OPERATORとして、Spoonからのイベントだけを安全かつ迅速に受信したい。

受入条件:
1. WHEN API Gateway HTTP APIが `POST /webhook` を受信したとき、THE SYSTEM SHALL LambdaへPayload Format Version 2.0のイベントを渡す。
2. WHEN `POST /webhook` 以外の経路または許可されないHTTPメソッドが要求されたとき、THE SYSTEM SHALL 業務処理を実行しない。
3. WHEN API Gatewayイベントの `isBase64Encoded` が真のとき、THE SYSTEM SHALL 本文を復号し、署名検証対象となる元のバイト列を保持する。
4. WHEN リクエストを処理したとき、THE SYSTEM SHALL `Content-Type: application/json` の定型レスポンスを返し、秘密情報や内部例外を応答本文に含めない。

### R2. Webhook認証
**ユーザーストーリー:** OPERATORとして、改ざんまたは偽装されたWebhookを拒否したい。

受入条件:
1. WHEN Webhookを受信したとき、THE SYSTEM SHALL JSON解析や副作用の前に、公式仕様で定められた署名ヘッダーとアルゴリズムを用いて未加工本文の署名を検証する。
2. WHEN 署名ヘッダーが欠落または署名が一致しないとき、THE SYSTEM SHALL `401 Unauthorized` を返し、イベント保存、コマンド実行、外部API呼び出しを行わない。
3. WHEN ヘッダー名の大文字・小文字が異なるとき、THE SYSTEM SHALL HTTP仕様に従い同一ヘッダーとして扱う。
4. WHEN 署名を比較するとき、THE SYSTEM SHALL 定時間比較を使用する。
5. WHEN 共有シークレットを必要とするとき、THE SYSTEM SHALL SSM SecureStringから復号取得し、ログまたはエラー応答に出力しない。

### R3. ペイロード検証と冪等性
**ユーザーストーリー:** HOSTとして、再送WebhookによってBot処理が重複しないようにしたい。

受入条件:
1. WHEN 本文が不正なJSON、または公式イベントスキーマ上の必須項目が不足しているとき、THE SYSTEM SHALL `400 Bad Request` を返し、副作用を発生させない。
2. WHEN 有効なイベントを受信したとき、THE SYSTEM SHALL ハンドラー実行前に `(live_session_id, event_id)` を条件付きで記録する。
3. WHEN 同一 `(live_session_id, event_id)` が再送されたとき、THE SYSTEM SHALL 業務処理をスキップして `200 OK` を返す。
4. WHEN 並行して同一イベントを受信したとき、THE SYSTEM SHALL 高々1件だけに副作用の実行権を与える。
5. WHEN イベント記録を保存したとき、THE SYSTEM SHALL 承認済み保有期間に基づくTTLを設定する。

### R4. イベントディスパッチ
**ユーザーストーリー:** HOSTとして、イベント種別に応じた適切な配信支援を受けたい。

受入条件:
1. WHEN `live.join` を受信したとき、THE SYSTEM SHALL 入室ハンドラーへ委譲し、設定で有効な場合にウェルカム応答を生成する。
2. WHEN `live.comment` を受信したとき、THE SYSTEM SHALL コメントハンドラーへ委譲する。
3. WHEN `live.gift` を受信したとき、THE SYSTEM SHALL ギフトハンドラーへ委譲し、設定に従いお礼応答と配信内集計を行う。
4. WHEN `live.end` を受信したとき、THE SYSTEM SHALL セッションを終了状態にし、残存一時データへ削除期限を設定する。
5. WHEN 未知のイベント種別を受信したとき、THE SYSTEM SHALL 警告として記録し、副作用なしで `200 OK` を返す。

### R5. コメントコマンド解析
**ユーザーストーリー:** 配信参加者として、チャットからBot機能を利用したい。

受入条件:
1. WHEN コメントが `!` または `/` で始まるとき、THE SYSTEM SHALL コマンド名と引数を解析する。
2. WHEN コメントがコマンド接頭辞で始まらないとき、THE SYSTEM SHALL コマンド処理を行わない。
3. WHEN 空コマンド、未知コマンド、不正な引数を受信したとき、THE SYSTEM SHALL 設定された簡潔な案内を返すか沈黙し、例外でWebhook処理を失敗させない。
4. THE SYSTEM SHALL コマンド名を正規化し、コマンド定義を一元管理する。
5. THE SYSTEM SHALL 少なくとも資料に例示された `help`、`song`、`dice` と、HOST/MANAGER向け運営コマンドを登録可能にする。ただし正確なコマンド一覧と意味は実装前承認事項とする。

### R6. ロールベース権限制御
**ユーザーストーリー:** HOSTとして、管理操作を許可されたユーザーだけに限定したい。

受入条件:
1. WHEN コマンドを実行するとき、THE SYSTEM SHALL 公式ペイロードまたは承認済みセッション設定から `HOST`、`MANAGER`、`LISTENER` を判定する。
2. WHEN LISTENERが管理コマンドを要求したとき、THE SYSTEM SHALL コマンドを実行せず、既定ではチャット応答を行わない。
3. WHEN MANAGERがHOST専用コマンドを要求したとき、THE SYSTEM SHALL コマンドを実行しない。
4. WHEN ロールを確実に判定できないとき、THE SYSTEM SHALL 最小権限としてLISTENER扱いにする。
5. THE SYSTEM SHALL ロール判定に暗号化された固有識別子のみを使用し、元のSpoon User IDの復号や配信横断照合を試みない。

### R7. OAuth 2.0認証・Spoon Open API送信
**ユーザーストーリー:** HOSTとして、自分が明示的に許可した範囲でBotに応答してほしい。

受入条件:
1. THE SYSTEM SHALL Spoon公式OAuth 2.0認可コードフローを採用し、固定共有APIトークンによる代替を行わない。
2. WHEN HOSTが連携を開始するとき、THE SYSTEM SHALL AWS認証済みOPERATORが実行するセットアップコマンドで推測困難な `state` を発行・保存し、公式仕様で対応する場合はPKCEを使用して認可画面URLを生成する。
3. WHEN 公開されたOAuth callbackを受信したとき、THE SYSTEM SHALL `state` と有効期限を検証してから認可コードをトークンへ交換する。
4. THE SYSTEM SHALL 機能に必要な最小スコープだけを要求する。
5. THE SYSTEM SHALL OAuthクライアントシークレット、アクセストークン、リフレッシュトークンをSSM Parameter Store Standard TierのSecureStringとして暗号化保存する。MVPは単一HOSTを対象とする。
6. WHEN アクセストークンの期限が近い、または公式APIが認証期限切れを返したとき、THE SYSTEM SHALL リフレッシュトークンで一度だけ更新し、更新結果を原子的に保存する。
7. WHEN トークンが失効、取消、更新不能、または不足スコープであるとき、THE SYSTEM SHALL API呼び出しを停止し、再認可が必要な状態として秘密情報を含まない運用ログへ記録する。
8. WHEN Spoon Open APIへ接続するとき、THE SYSTEM SHALL HTTPS、Bearer認証、公式に承認されたエンドポイントのみを使用する。
9. WHEN HOSTが連携解除するとき、THE SYSTEM SHALL 公式の失効APIがあればトークンを失効させ、保存済みトークンとOAuth一時状態を削除する。
10. THE SYSTEM SHALL クライアントシークレット、各種トークン、Webhookシークレットをソースコード、CIログ、CloudWatch Logsへ出力しない。

### R8. レート制限とクールダウン
**ユーザーストーリー:** OPERATORとして、Spoon APIへ過負荷を与えず規約と公式レート制限を守りたい。

受入条件:
1. WHEN 同一スコープのコマンドが5秒以内に再実行されたとき、THE SYSTEM SHALL DynamoDBの原子的な判定により送信を抑制し、`200 OK` を返す。
2. WHEN Open APIが `429 Too Many Requests` を返したとき、THE SYSTEM SHALL `Retry-After` が公式に提供される場合はそれを優先し、それ以外はジッター付き指数バックオフを行う。
3. WHEN 最大2回の再試行またはLambdaの安全な残り実行時間を超えるとき、THE SYSTEM SHALL 再試行を停止し、Webhookには `200 OK` を返してメトリクスとエラーログを記録する。
4. THE SYSTEM SHALL 公式APIドキュメントの呼出制限を設定可能にし、複数認証情報による制限回避を行わない。
5. WHEN 外部APIがタイムアウトまたは一時的5xxを返したとき、THE SYSTEM SHALL 公式仕様と実行時間予算に従って限定再試行または安全な失敗を行う。

### R9. 状態管理とデータ削除
**ユーザーストーリー:** リスナーとして、自分の活動情報が必要最小限かつ短期間だけ扱われてほしい。

受入条件:
1. THE SYSTEM SHALL 承認されたBot機能に必要な最小限の活動情報だけを保存する。
2. THE SYSTEM SHALL 活動情報を既定24時間以内の論理有効期限で失効させ、DynamoDB TTLによる物理削除対象にする。
3. WHEN 配信が終了、アプリ提供が終了、利用契約が終了、または削除要求が発生したとき、THE SYSTEM SHALL 対象活動情報を復元困難な方法で速やかに削除できる。
4. THE SYSTEM SHALL TTL削除が即時ではないことを考慮し、期限切れデータを業務処理で読み取らない。
5. THE SYSTEM SHALL 活動情報を目的外利用、販売、公開、無断第三者提供、AI/MLの学習・評価に利用しない。

### R10. ログ・監視・事故対応
**ユーザーストーリー:** OPERATORとして、個人情報を漏らさず障害と不正利用を検知したい。

受入条件:
1. THE SYSTEM SHALL JSON構造化ログを出力し、相関ID、イベント種別、匿名化されたイベント識別子、結果、レイテンシを含める。
2. THE SYSTEM SHALL APIトークン、シークレット、Authorizationヘッダー、コメント全文、ニックネーム、活動情報の生値をログに含めない。
3. WHEN 署名失敗、429、外部API失敗、Lambdaエラーが閾値を超えたとき、THE SYSTEM SHALL CloudWatch Alarm等でOPERATORへ通知する。
4. THE SYSTEM SHALL セキュリティ事故時にトークン停止・ローテーション、影響調査、Spoonへの通知、再発防止を実施できる運用手順を備える。
5. THE SYSTEM SHALL クライアントシークレットの3年ごとの更新、および必要時の緊急更新を可能にする。

### R11. デプロイと変更管理
**ユーザーストーリー:** OPERATORとして、長期AWSキーを使わず再現可能かつ安全にデプロイしたい。

受入条件:
1. THE SYSTEM SHALL AWS SAMでAPI Gateway、Lambda、DynamoDB、SSM参照権限、ログ、監視をコード化する。
2. WHEN GitHub Actionsからデプロイするとき、THE SYSTEM SHALL OIDCフェデレーションによる短期STS認証を使用し、長期AWSアクセスキーを保存しない。
3. THE SYSTEM SHALL Lambda IAMロールおよびCI/CDロールへ最小権限を付与する。
4. WHEN 主要機能、目的、権限範囲、データ利用・保存方法、保有期間、運営主体を変更するとき、THE SYSTEM SHALL 本番適用前のSpoonへの報告・再審査要否確認をリリース手順に含める。
5. WHEN Spoonの審査・運用承認を得ていないとき、THE SYSTEM SHALL 開発・テスト環境以外への公開を禁止する運用ゲートを設ける。

### R12. 利用者告知と表示
**ユーザーストーリー:** リスナーとして、配信で外部Botが活動情報を処理することを認識したい。

受入条件:
1. THE SYSTEM SHALL Spoonが提供するBot利用・活動情報処理の告知を削除、遮断、改変しない。
2. THE SYSTEM SHALL Bot使用状態をSpoonへ正確に通知できる。
3. THE SYSTEM SHALL Spoon Developers APIを利用していることをSpoon指定の方法で表示し、公式サービスまたは提携サービスと誤認させない。

## 5. HTTP結果契約

| 条件 | HTTP | 副作用 |
|---|---:|---|
| 正常処理 | 200 | 許可された処理を実行 |
| 重複イベント | 200 | なし |
| クールダウン、再試行枯渇 | 200 | 送信なし、監視記録あり |
| 未知イベント | 200 | なし |
| JSON不正・必須項目不足 | 400 | なし |
| 署名欠落・不一致 | 401 | なし |
| 予期しない内部障害 | 500 | 重複実行安全性を維持し、詳細非公開 |

## 6. 非機能要件

### NFR1. 可用性
- Bot管理下のWebhook受信境界について月間99.95%を目標とする。SpoonおよびAWSの外部SLAは測定範囲を分離する。
- 単一AZ依存のリソースを置かない。

### NFR2. 性能
- 重複・不正イベントは外部APIを呼び出さず速やかに応答する。
- 通常イベントのWebhook応答は p95 3秒以内を目標とし、Spoon Open API待機時間は別メトリクス化する。
- Lambdaは256MB、arm64、最大10秒を初期値とし、負荷試験結果に基づき調整する。

### NFR3. スケーラビリティ
- Lambda同時実行とDynamoDBオンデマンドによりスパイクへ追従する。
- グローバルなクールダウンと冪等性はLambdaメモリに依存しない。

### NFR4. セキュリティ
- 通信はAWS/Spoonが対応する強いTLS設定を用い、最低TLS 1.2を必須とし、TLS 1.3は対応可否確認後に採用する。
- 保存時暗号化、最小権限、秘密情報の分離、依存関係スキャンを適用する。

### NFR5. コスト
- NAT Gateway、常時稼働サーバー、Secrets Manager、SQS、カスタマー管理KMS鍵など固定費または追加課金要因をMVPでは使用しない。
- API Gateway HTTP API、Lambda、DynamoDB On-Demand、SSM Parameter Store Standard Tier、AWS管理キー、短期保持のCloudWatch Logsを用い、低トラフィック時にAWS無料利用枠内となることを目標とする。
- AWS標準メトリクスと無料枠内のアラームを優先し、有料カスタムメトリクスは導入しない。AWS Budgetsで上限通知を設定する。
- 月額 `$0.00` は利用量・無料枠・AWS料金改定に依存するため保証せず、デプロイ前のコスト見積りと月次利用量確認を必須とする。

### NFR6. 保守性・テスト容易性
- 外部I/Oをインターフェースで分離し、時刻・スリープ・HTTP・AWSサービスをテストで差し替え可能にする。
- 公式イベント/API仕様変更をスキーマと設定の更新で追従可能にする。

## 7. データ分類

| データ | 分類 | 保存方針 |
|---|---|---|
| クライアントシークレット、Webhookシークレット、トークン | Secret | SSM SecureString等。ログ禁止、最小権限 |
| 暗号化されたリスナー識別子、活動情報 | Confidential/PII相当 | 必要最小限、既定24時間、目的外利用禁止 |
| コメント本文、ニックネーム | Confidential | 原則永続保存・ログ出力しない |
| イベントID、配信ID | Internal | ハッシュ化ログ可、TTL対象 |
| 集計値、設定値 | Internal | 個人へ逆引きできない形で保存 |

## 8. 規約トレーサビリティ

| 規約 | 要求への反映 |
|---|---|
| 第5・7条 | 承認済みAPI・スコープ・レート制限のみ使用 |
| 第6条 | 実運用前審査、提出情報と実装の一致 |
| 第8条 | OAuth 2.0、明示的委任、最小スコープ |
| 第9条 | 秘密管理、ローテーション、事故通知 |
| 第10条 | 重要変更前の報告・再審査 |
| 第11条 | リスナー告知を妨げない |
| 第12・13条 | 最小データ、目的制限、削除、追跡・AI利用禁止 |
| 第14条 | 過負荷・制限回避・非公開API・誤認表示の禁止 |
| 第17・18条 | 停止時のAPI利用中止とデータ削除 |

## 9. 前提・確定方針
- 対象リージョンは `ap-northeast-1`。
- ランタイムはPython 3.12、IaCはAWS SAM、テストはpytestを採用する。
- 認証はOAuth 2.0認可コードフローとし、MVPは単一HOSTを対象にOAuthクライアント情報とトークンをSSM Parameter Store Standard TierのSecureStringへ保存する。
- イベント処理はDynamoDBで `PROCESSING/SUCCEEDED/RETRYABLE_FAILED` の状態を管理し、一時障害時に安全に回復可能とする。
- Spoon APIへの送信はLambda内の同期・best-effort処理とし、SQS等の追加サービスはMVPで採用しない。重要な状態更新を先に永続化し、チャット応答失敗で状態更新を失わないようにする。
- Spoon公式API仕様が入力資料にないため、イベント名、ヘッダー名、ペイロード項目、OAuth URL、PKCE対応、スコープ、API URL、レート上限の具体値のみ、公式仕様入手後に確定する。

## 10. 完了条件
- 全必須要件に対応する実装と自動テストが存在する。
- Spoon公式APIドキュメントとの契約テストが合格する。
- セキュリティ、データ保有、IAM、ログマスキングのレビューが完了する。
- Spoonによる審査・認証・運用承認を得るまでは本番利用しない。

## 11. 実装前に解消する未確定事項

### P0（公式仕様待ち。具体値のみ実装をブロック）
1. Spoon公式Webhook仕様: 実在するイベント名、署名ヘッダー名、署名対象バイト列、エンコード、再送条件。
2. Spoon OAuth/API仕様: 認可・トークン・失効URL、PKCE対応、必要スコープ、トークン更新方式、チャット送信API。
3. 各イベントのJSON Schemaとサンプルペイロード。
4. 正式なコマンド一覧、引数、応答、ロール別許可表。特に `song` が曲リクエスト登録か現在曲照会かを確定する。
5. MANAGERの登録・解除主体、保存方法、配信単位か恒久設定か。
6. ギフト集計項目と保存期間、ウェルカム/お礼メッセージの頻度・文面。
7. Spoonの実際のレート制限値と `Retry-After` 仕様。

### P1（本番前に確認）
1. MVPの24時間保有がSpoon審査で承認されるか、および契約終了時の一括削除手順。
2. SLAの測定境界、アラーム通知先、RTO/RPOの具体値。
3. 無料利用枠の適用条件と想定イベント量に基づく月額見積り。無料枠超過時は機能停止ではなく予算通知を行う。
4. 設計書の最終更新日 `2026/10/14` が作成時点 `2026/10/13` より未来であるため、文書管理日付を確認する。
