# ソースコード詳細設計書

## 1. 文書管理情報
- **システム名**: 配信マネージャーシステム (`Lab_Singing`)
- **設計項目**: ソースコード・モジュール詳細設計
- **ステータス**: 確定
- **優先度**: 高
- **設計担当者**: 清水 康太
- **最終更新日**: 2026/10/14
- **管理No.**: CODE-01
- **対象スコープ**: Python 3.12 Lambda関数 実装構造・関数仕様・例外設計・テスト仕様

---

## 2. ディレクトリ構造

```text
SpoonLab_Singing/
├── template.yaml                # AWS SAM テンプレート
├── requirements.txt             # プロダクション依存パッケージ
├── requirements-dev.txt         # 開発・テスト依存パッケージ (pytest, moto等)
├── src/
│   ├── __init__.py
│   ├── app.py                   # Lambdaエントリーポイント
│   ├── auth.py                  # HMAC署名検証・SSMシークレット取得
│   ├── dispatcher.py            # イベント振り分けロジック
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── join_handler.py      # 入室イベント処理
│   │   ├── comment_handler.py   # コメント解析・マネージャー処理
│   │   ├── gift_handler.py      # ギフト受信処理
│   │   └── end_handler.py       # 配信終了処理
│   ├── services/
│   │   ├── __init__.py
│   │   ├── dynamo_service.py    # DynamoDB I/O (冪等性・セッション)
│   │   └── api_client.py        # 外部API呼び出し (レート制御・バックオフ)
│   └── utils/
│       ├── __init__.py
│       └── logger.py            # 構造化ロガー・PIIマスキング
└── tests/
    ├── __init__.py
    ├── conftest.py              # テストフィクスチャ・モック設定
    ├── test_auth.py             # 署名検証単体テスト
    ├── test_dispatcher.py       # イベント振分単体テスト
    ├── test_handlers.py         # 各ハンドラー単体テスト
    └── test_api_client.py       # レート制御・バックオフテスト
```

---

## 3. 主要モジュール・関数詳細仕様

### 3.1. `src/app.py`
Lambdaのエントリーポイント。リクエスト受信、署名検証、ディスパッチ、統一レスポンス返却を統括。

```python
def lambda_handler(event: dict, context: object) -> dict:
    """API Gateway HTTP APIイベントハンドラー
    Args:
        event (dict): HTTPリクエストイベント (headers, body 等)
        context (object): Lambdaランタイムコンテキスト
    Returns:
        dict: API Gateway レスポンスオブジェクト (statusCode, headers, body)
    """
```
- **処理ロジック**:
  1. `event` からリクエストヘッダーおよび生の本文（`body`）を抽出。
  2. `auth.verify_signature(headers, body)` を実行。失敗時は即座に `{"statusCode": 401, "body": "Unauthorized"}` を返却。
  3. `body` を JSON パースし、`dispatcher.dispatch_event(payload)` を呼び出し。
  4. 正常終了時は `{"statusCode": 200, "body": "OK"}` を返却。
  5. 捕捉されない例外発生時は `utils.logger` に記録し `{"statusCode": 500, "body": "Internal Server Error"}` を返却。

---

### 3.2. `src/auth.py`
HMAC-SHA256署名検証およびSSMシークレットの安全な取り扱い。

```python
# グローバルキャッシュ変数
_CACHED_SECRET: str | None = None

def get_webhook_secret() -> str:
    """SSM Parameter Storeから共有シークレットを取得 (インメモリキャッシュ)"""

def verify_signature(headers: dict, body_str: str) -> bool:
    """Webhookリクエストの署名検証
    Args:
        headers (dict): リクエストヘッダー辞書
        body_str (str): 受信した未加工のBody文字列
    Returns:
        bool: 検証成功時 True, 失敗時 False
    """
```
- **署名生成方式**: `hmac.new(secret.encode('utf-8'), body_str.encode('utf-8'), hashlib.sha256).hexdigest()`
- **照合方式**: タイミング攻撃を排除するため `hmac.compare_digest(calculated_signature, request_signature)` を使用。

---

### 3.3. `src/dispatcher.py`
イベントの種別判定と各ハンドラーへの委譲。

```python
def dispatch_event(payload: dict) -> dict:
    """イベント種別に応じたハンドラーの実行
    Args:
        payload (dict): パース済みWebhookペイロード
    Returns:
        dict: 処理結果サマリ
    """
```
- `event_type = payload.get("event_type")` を評価。
- 未知のイベント種別を受信した場合はログに記録した上で正常終了（前方互換性維持）。

---

### 3.4. `src/handlers/comment_handler.py`
コメントコマンドの解析と権限別ロジック実行。

```python
def handle_comment_event(payload: dict) -> None:
    """コメント受信時の処理ハンドラー"""

def parse_command(comment_text: str) -> tuple[str, list[str]] | None:
    """コマンド構文解析
    Returns:
        tuple (command_name, args) または None
    """
```
- `!` または `/` を検出した場合、スペース区切りでコマンド名と引数リストに分割。
- ユーザーロール（`host` / `manager` / `listener`）と許可コマンドテーブルを照合し、権限不足の場合は沈黙（チャットを汚さない配慮）。

---

### 3.5. `src/services/dynamo_service.py`
状態永続化、二重実行防止、自動消去TTL管理。

```python
def save_event_idempotent(session_id: str, event_id: str) -> bool:
    """イベントの二重処理チェックおよび記録
    Returns:
        bool: 新規記録成功時 True (処理続行), 既存レコード存在時 False (二重実行抑止)
    """
```
- DynamoDB `put_item` に対して `ConditionExpression='attribute_not_exists(PK)'` を適用。
- `ConditionalCheckFailedException` 発生時は `False` を返し、ハンドラー側で重複イベントとしてスキップ。
- 属性 `ttl_timestamp` に `int(time.time()) + 86400`（24時間後）を設定。

---

### 3.6. `src/services/api_client.py`
外部APIとの通信、クールダウン判定、指数バックオフ。

```python
def post_chat_message(live_id: str, message: str) -> bool:
    """外部プラットフォームへのチャットメッセージ送信"""
```
- 直前送信時刻との差分を検証（クールダウン: 5秒）。
- レスポンスステータス `429` 検出時:
  ```python
  for attempt in range(max_retries):
      sleep_time = base_delay * (2 ** attempt)
      time.sleep(sleep_time)
      # 再試行リクエスト実行
  ```

---

## 4. 例外ハンドリング & HTTPステータスマッピング

| 内部例外クラス | 発生契機 | 返却HTTPステータス | ロギングレベル |
| :--- | :--- | :---: | :--- |
| `SignatureVerificationError` | 署名ヘッダー欠落、HMACハッシュ不一致 | `401 Unauthorized` | WARN（IP・UserAgent記録） |
| `DuplicateEventError` | 同一EventIdの再送検知 | `200 OK` | INFO（スキップ記録） |
| `CooldownActiveError` | 5秒以内の同一コマンド連打 | `200 OK` | INFO（送信抑制記録） |
| `ExternalApiRateLimitError` | 指数バックオフ上限超過 | `200 OK` | ERROR（破棄記録） |
| `InvalidPayloadError` | JSON構文エラー、必須フィールド欠落 | `400 Bad Request` | WARN |
| `Exception` (汎用例外) | 予期せぬ実行時ランタイムエラー | `500 Internal Error` | ERROR（スタックトレース出力） |

---

## 5. 単体テスト方針 (`tests/`)

1. **テストフレームワーク**: `pytest`
2. **モックライブラリ**:
   - AWSサービス（SSM / DynamoDB）: `moto` を用いてローカルインメモリ環境で検証。
   - 外部HTTP通信: `unittest.mock` または `responses` を用いてモック化（実通信の発生を防止）。
3. **テスト重点項目**:
   - `test_auth.py`: 正常署名、不正署名、署名ヘッダー欠落時の挙動。
   - `test_dynamo_service.py`: 条件付き書き込みによる冪等性の担保、TTL属性の正確な付与。
   - `test_api_client.py`: 429エラー時の指数バックオフ秒数計算とリトライ上限処理。