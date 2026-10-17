# Spoon Open API 契約メモ

Spoon Developers公式ドキュメント（2026-10-17時点）から、実装に必要な外部仕様を要約したもの。要件・設計はこの文書を正とし、公式ドキュメントが更新されたらここを先に直す。

- 出典の確度: **確認済み** = 公式ページ全文（PDF）で確認 / **抜粋** = 公式ページの検索抜粋のみで確認 / **未確認** = 公式情報なし
- 公式内容はライセンス遵守のため引用せず、要約・再構成している。

## 1. 接続先（抜粋）

| 用途 | 日本 | 韓国 |
|---|---|---|
| Open API base URL | `https://jp-openapi.spooncast.net` | `https://kr-openapi.spooncast.net` |
| DJ同意画面 | `https://developers.spooncast.net/jp/oauth/authorize` | `.../kr/oauth/authorize` |

- DJのアカウント地域がregionを決める。別regionのURLではそのDJの配信を見つけられない。
- MVPは日本regionのみをallowlistに入れる。

## 2. 認証・トークン

| 項目 | 内容 | 確度 |
|---|---|---|
| 同意画面パラメータ | `response_type=code`, `client_id`, `redirect_uri`, `scope`（空白区切り） | 抜粋 |
| redirect_uri | 登録値と1文字でも違うとエラー | 抜粋 |
| 認可コード | 60秒以内にtokenへ交換 | 確認済み |
| token endpoint | `POST /v1/oauth/token`（`grant_type` は `authorization_code` / `refresh_token`） | 確認済み |
| revoke endpoint | `POST /v1/oauth/revoke` | 確認済み |
| OAuth endpointの認証 | Basic認証 + `application/x-www-form-urlencoded` | 抜粋 |
| Bot APIの認証 | `Authorization: Bearer {access_token}`、bodyは `application/json` | 抜粋 |
| access token有効期限 | 発行から1時間 | 確認済み |
| refresh token有効期限・ローテーション | — | **未確認** |
| PKCE・state | — | **未確認**（stateは常に付与。PKCEは対応確認後に有効化） |
| トークンとDJ | 1 token = 1 DJの同意。path/queryでDJを指定しない | 確認済み |

OAuth endpointのエラーはRFC 6749形式（`error` / `error_description`）。

| error | HTTP | Botの対応 |
|---|---:|---|
| `invalid_request` | 400 | リクエスト不備。修正するまで再送しない |
| `invalid_client` | 401 | Client ID/Secret不一致。設定を確認 |
| `invalid_grant` | 400 | codeの期限切れ・使用済み、またはrefresh_token失効。refresh失敗ならDJに再連携を案内 |
| `unsupported_grant_type` | 400 | 実装バグ |

## 3. 権限（scope）

公式は11種類。確認できたもの:

| 権限 | 用途 | 確度 |
|---|---|---|
| `events.chat` | チャット受信 | 確認済み |
| `events.presence` | 入場受信（**Botがマネージャーである必要あり**） | 確認済み |
| `events.like` | ハート受信 | 確認済み |
| `events.donation` | ギフト受信 | 確認済み |
| `chat.send` | Bot名義チャット送信 | 抜粋 |
| `live.read` | 現在の配信情報 `GET /v1/live` | 抜粋 |
| 残り5種類 | — | **未確認** |

MVPの要求scope: `events.chat chat.send`。入場・ハート・ギフト機能を有効にする場合だけ該当 `events.*` を追加する。

## 4. イベントストリーム `GET /v1/live/events`（確認済み）

### 4.1 リクエスト
- ヘッダー: `Authorization: Bearer ...`、`Accept: text/event-stream`。path/query/bodyなし。
- `events.chat` / `events.presence` / `events.like` / `events.donation` のいずれか1つ以上が必要。1つもなければ403。
- 同意されていないイベントは単に届かない（接続は正常）。
- 呼び出し上限（rate limit）の対象外。上限ヘッダーも付かない。
- gzipされない。

### 4.2 接続の副作用
- 接続するとBotアカウントがリスナーとして配信の部屋へ入場する。
- Bot自身はイベント・リスナー一覧・ファンランキングに出ない。
- DJの強制退出・チャット禁止・凍結がそのままBotに効く。
- 接続中は、Bot名義のリスナー向け告知が投稿される（無効化不可）。
- 切断してもBotは部屋に残り、再接続しても入場メッセージは再表示されない。
- Botが部屋から抜けるのは「DJが強制退出させた」「最初の入場から5時間で入場記録が失効した（延長なし）」の2つ。その後の再接続またはチャット送信は新しい入場として扱われ、入場メッセージが再表示される。
- 同じアプリが同じDJへ開けるストリームは1本。新しい接続が入ると、古い接続は15秒以内に `DUPLICATE_CONNECTION` で閉じる。

### 4.3 フレーム
```text
:heartbeat

id:1788328046114-0
event:chat
data:{...json...}
```
- `:` で始まる行はcomment（heartbeat）。既定15秒ごと。`data:` のないフレームは読み飛ばす。
- `id:` は `{epochMillis}-{offset}`。**現在は再開に使えない**（`Last-Event-ID` 未対応）。切れていた間のイベントは受け取れない。
- 時刻はISO-8601 UTC（`Z`）。小数部の桁数を固定しない。

### 4.4 イベント

共通 `user`:

| フィールド | 型 | null | 説明 |
|---|---|---|---|
| `user.id` | string | 不可 | 暗号化ID。BotとそのDJの組合せで一意・不変。再同意後も同じ。リスナー一覧・ファンランキングのidと同じ値 |
| `user.nickname` | string | 可 | 空文字ではなくnull。識別に使わない |

`chat`（`events.chat`）: リスナーのチャットとDJのクイックメッセージ。

| フィールド | 型 | null | 説明 |
|---|---|---|---|
| `isDj` | boolean | 不可 | DJ本人の発言か。chatにだけあり、トップレベル |
| `message` | string | 不可 | 原文。JSONに見えてもparseしない |
| `sentTime` | string | 不可 | ISO-8601 UTC |

- チャット禁止のリスナーの発言はイベント自体が作られない。
- ギフトメッセージ・運営のお知らせ・システム発言・Bot自身の発言は届かない。同じ配信の**他のBotの発言は届く**。
- 発言者がマネージャーかどうかの情報は**含まれない**。

`presence`（`events.presence`）: 入場。

| フィールド | 型 | null | 説明 |
|---|---|---|---|
| `type` | string | 不可 | 現在は `JOIN` のみ。退場は届かない |
| `fanRank` | number | 可 | この配信のギフトランキング順位 |
| `isManager` | boolean | 不可 | DJがこのリスナーをマネージャーに指定しているか |
| `favoriteTemperature` | number | 可 | 愛聴温度。常連判定のしきい値はBotが決める。nullなら判定しない |
| `time` | string | 不可 | ISO-8601 UTC |

- Botがマネージャーでないと1件も届かない（エラーにはならない）。
- 同じ人が出入りすると再び届く。`user.id` で重複を除く。

`like`（`events.like`）: ハート。

| フィールド | 型 | null | 説明 |
|---|---|---|---|
| `type` | string | 不可 | `PAID`（スプーンを使ったアイテム）/ `FREE` |
| `totalAmount` | number | 不可 | 今回加算された総数 = `(amount + extraAmount) × combo`。**集計にはこれを使う** |
| `amount` | number | 不可 | アイテム1つのハート数 |
| `extraAmount` | number | 不可 | ブースト分。なければ0 |
| `combo` | number | 可 | 無料タップハートはnull |
| `time` | string | 不可 | ISO-8601 UTC |

- FREEも集計に含める。反応はPAIDだけにし、FREEに反応するならクールダウンを置く。

`donation`（`events.donation`）: ギフト。

| フィールド | 型 | null | 説明 |
|---|---|---|---|
| `amount` | number | 不可 | 合計スプーン数（掛け算済み）。`like.amount` とは単位が違う |
| `message` | string | 可 | 一言。nullの方が多い |
| `time` | string | 不可 | ISO-8601 UTC |

`end`（権限不要）: ストリームが閉じる理由。この後は何も届かない。

| reason | 意味 | Botの対応 |
|---|---|---|
| `LIVE_ENDED` | 配信終了 | 再接続しない |
| `TOKEN_EXPIRED` | access token失効（1時間ごとに正常に発生） | refreshしてすぐ再接続 |
| `RECONNECT` | サーバーがこの接続だけ閉じた | バックオフなしですぐ再接続（tokenはそのまま） |
| `DUPLICATE_CONNECTION` | 同じアプリの別接続が引き継いだ | 再接続しない |
| （endなしで切断） | ネットワーク切断 | 1→2→4秒…最大30秒 + jitterで再接続 |

### 4.5 接続ループ
- 配信中かを事前確認しない。配信中でなければ接続時に404（`OAPI_MNGR_0301`）。
- 404は即座に再試行せず、数秒の間隔を置く。
- エラーもSSE形式（bodyが `data:` で始まる）。HTTPステータスで先に分岐し、bodyが必要なら `data:` を外してparseする。

| HTTP | detailCode | 意味 | 対応 |
|---|---|---|---|
| 401 | なし | token無効 | refreshを1回、それでも401ならDJに再連携を案内 |
| 403 | なし | `events.*` 権限がない | 再同意を案内 |
| 403 | `OAPI_MNGR_0209` | 入場できない（DJがBotをブロックした可能性） | 停止しDJに案内 |
| 404 | `OAPI_MNGR_0301` | 配信中でない | バックオフして再接続 |
| 502 | `OAPI_MNGR_2302` | 配信サーバーエラー | バックオフして再試行 |

## 5. マネージャー指定（確認済み）

- 指定はDJがSpoonアプリで行う。APIでは要求できない。
- 推奨は固定マネージャー。BotアカウントがDJを先にフォローしている必要がある（配信中に `POST /v1/live/follow`、またはBotアカウントでアプリから直接）。
- 固定マネージャーはDJの配信中には変更できない。フォローは配信中しかできない。→ 配信中にフォローし、配信後にDJが指定すると次回配信から有効。
- 配信ごとの指定は最大3名。部屋にいる人だけ指定できる。指定・解除は数秒で反映され、再接続は不要。
- マネージャーになると、マネージャー等級のメッセージも届く。DMはどの場合も届かない。

## 6. チャット送信 `POST /v1/live/chat`

| 項目 | 内容 | 確度 |
|---|---|---|
| 表示 | Botアカウントのニックネームで投稿 | 抜粋 |
| 本文 | 最大200文字。絵文字は2文字と数えられることがある。空・空白のみは400 | 確認済み |
| requestの項目名・response | — | **未確認** |
| 冪等性・再送安全性 | — | **未確認** |

## 7. 共通エラー（確認済み）

Bot API（OAuth endpoint以外）はRFC 7807形式:
`instance`, `status`, `title`, `detailCode`, `message`, `userId`, `extra`, `timestamp`

- 分岐は `detailCode` だけで行う。`message` は文言が変わり得るため、分岐にもユーザー表示にも使わない。
- `userId` / `extra` は常にnull。未知フィールドは無視する（schemaで追加項目を禁止しない）。
- ゲートウェイで止められた401/403/429には `detailCode` がない。ステータスで先に分岐する。
- **401と403は別に扱う**（401は再連携、権限不足の403は再同意だけで済む）。401を無限に再試行しない。

| HTTP | detailCode | 意味 | 対応 |
|---|---|---|---|
| 400 | `OAPI_MNGR_0103` | 形式不正（body/必須項目/Content-Type/メソッド） | 修正するまで再送しない |
| 400 | `OAPI_MNGR_0108` | メッセージが空 | 送信前に検証 |
| 400 | `OAPI_MNGR_0109` | 200文字超過 | 送信前に検証 |
| 401 | なし / `OAPI_MNGR_0203` | token無効（`0203` は破棄直後1分以内） | refresh 1回 → 失敗なら停止・再連携 |
| 403 | なし | 権限不足 | 再同意 |
| 403 | `OAPI_MNGR_0208` | チャット凍結中またはBotがチャット禁止 | 少し後に再試行、続けばDJに案内 |
| 403 | `OAPI_MNGR_0209` | 入場不可（ブロックの可能性） | DJが解除するまで停止 |
| 403 | `OAPI_MNGR_0211` | BotがDJをフォローできない（ブロックの可能性） | DJが解除するまで停止 |
| 404 | `OAPI_MNGR_0301` | 配信中でない（状態） | ストリームはバックオフ再接続、取得は後で再試行 |
| 429 | `OAPI_MNGR_0302` | チャット送信が多すぎる。配信全体の混雑でも発生 | 少し待って再送 |
| 429 | なし（body空） | 呼び出し上限（§8） | §8に従う |
| 500 | `OAPI_MNGR_1002` | サーバーエラー | 指数バックオフ |
| 502 | `OAPI_MNGR_2302` / `2303` | 配信 / チャットサーバーエラー | 指数バックオフ |

## 8. 呼び出し上限（確認済み）

| 上限 | 単位 | 超過時 | 対応 |
|---|---|---|---|
| 秒あたり | DJの連携ごと | 429、bodyなし、`Retry-After` なし | 指数バックオフ |
| 1日あたり | アプリ合計、かつ権限ごと | 429、bodyなし、`Retry-After` あり | `Retry-After` 秒待つ（次の0時リセットまで） |

- 正常レスポンスにも上限ヘッダーが付く。429になる前に速度を落とす。

| ヘッダー | 意味 |
|---|---|
| `x-ratelimit-remaining` | 残りの秒あたりトークン（0なら次は429） |
| `x-ratelimit-replenish-rate` | 1秒あたりの補充数 |
| `x-ratelimit-burst-capacity` | 瞬間的に使える上限 |
| `x-ratelimit-daily-limit` | 1日の上限 |
| `x-ratelimit-daily-remaining` | 今日の残り |
| `x-ratelimit-daily-reset` | 次のリセット時刻（epoch秒） |
| `x-ratelimit-daily-scope` | 1日上限の429にだけ付く。掛かった権限名。なければアプリ全体 |

- 連携（DJ）を増やしても1日の総量は増えない。
- リスナー向け告知はBotの上限に数えない。

## 9. 未確認事項
1. refresh tokenの有効期限、refresh時にrefresh tokenも新しくなるか（ローテーション）
2. PKCE対応有無、`state` の扱い、同意拒否時のエラーパラメータ
3. 11種類の権限の残り5種類
4. `POST /v1/live/chat` のrequest/response schema、再送の安全性
5. `POST /v1/live/follow` に必要な権限
6. 秒あたり・1日あたりの具体的な上限値（実レスポンスのヘッダーで確認する）

## 参照
- [Event stream](https://developers.spooncast.net/docs/ja/api-reference/live-events)（確認済み）
- [Error codes](https://developers.spooncast.net/docs/ja/api-reference/error-codes)（確認済み）
- [Build a bot](https://developers.spooncast.net/docs/en/getting-started/build-a-bot)
- [Choosing permissions](https://developers.spooncast.net/docs/en/getting-started/choosing-permissions)
- [Get a DJ's consent](https://developers.spooncast.net/docs/en/getting-started/user-authorization)
- [Tokens](https://developers.spooncast.net/docs/en/api-reference/tokens)
- [Chat](https://developers.spooncast.net/docs/en/api-reference/chat)
- [Listener notice](https://developers.spooncast.net/docs/en/getting-started/listener-notice)

Content was rephrased for compliance with licensing restrictions.
