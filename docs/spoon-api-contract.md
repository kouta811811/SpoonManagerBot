# Spoon Open API 契約メモ

Spoon Developers公式ドキュメント（2026-10-17時点）から、実装に必要な外部仕様を要約したもの。要件・設計はこの文書を正とし、公式ドキュメントが更新されたらここを先に直す。

- 出典の確度: **確認済み** = 公式ページ全文（`input/公式ドキュメント/` のテキスト）で確認 / **抜粋** = 公式ページの検索抜粋のみで確認 / **未確認** = 公式情報なし
- 公式内容はライセンス遵守のため引用せず、要約・再構成している。
- 2026-10-17更新: `input/公式ドキュメント/` に全ページの公式テキストが追加されたため、トークン・チャット送信・イベントストリーム・エラーコードを全文で再確認し、旧「未確認」を解消した。

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
| 同意画面パラメータ | `response_type=code`（code のみ）, `client_id`（UUID形式必須）, `redirect_uri`, `scope`（空白区切り・`%20`エンコード）, `state`（推奨・最大512バイト） | 確認済み |
| redirect_uri | 登録値と1文字でも違うとエラー。不一致時は自分のURLへ戻らずSpoon側にエラー表示 | 確認済み |
| 認可コード（code） | 60秒以内にtokenへ交換。1回限り。**同一codeを2回使うとその連携の全トークンが失効**（盗用シグナル扱い）。ネットワークエラーで応答を受け取れなくても同一codeで再試行せず、同意からやり直す | 確認済み |
| token endpoint | `POST /v1/oauth/token`（`grant_type` は `authorization_code` / `refresh_token`） | 確認済み |
| revoke endpoint | `POST /v1/oauth/revoke`（`token` 必須、`token_type_hint` は受理するが無視）。成功は200ボディなし。存在しない/他人のtokenでも200（RFC 7009）。refresh失効でaccessも道連れ。失効は「連携解除」ではなく同意は残る | 確認済み |
| OAuth endpointの認証 | `Content-Type: application/x-www-form-urlencoded`。クライアント認証は **`Authorization: Basic base64(client_id:client_secret)` が優先**、無ければform の `client_id`/`client_secret` にフォールバック。どちらも無い/`client_id` 非UUIDは `401 invalid_client`（`WWW-Authenticate: Basic realm="oauth"` 付き）。Client Secretを使うのでサーバーからのみ呼ぶ | 確認済み |
| Bot APIの認証 | `Authorization: Bearer {access_token}`、bodyは `application/json` | 確認済み |
| access token有効期限 | 発行から1時間（`expires_in` 既定3600秒） | 確認済み |
| refresh token有効期限 | 既定 **30日** | 確認済み |
| refresh token ローテーション | **あり**。更新で古いrefresh tokenは直ちに失効し、新しいaccess/refreshペアが返る。**新しい `refresh_token` を必ず保存**（保存漏れだと次回更新が `400 invalid_grant` でBot停止）。古いaccess tokenは道連れにせず自然失効（更新直後は2本のaccess tokenが一時的に両方有効） | 確認済み |
| PKCE | 公式ドキュメントに **記載なし＝未対応**。`code_challenge`/`code_verifier` は送らない | 確認済み（記載なし） |
| state | リクエストごとに新しいランダム値。最大512バイト。callbackにそのまま載るので照合して偽造を弾く | 確認済み |
| 更新時のscope確認 | レスポンスの `scope` はその時点のDJ同意に基づく。DJが同意を減らすと次回更新で自動反映。毎回確認する | 確認済み |
| 更新タイミング | 期限直前は危険。**5分前など大きめマージン**で更新し、結果を永続化（メモリのみだと再起動で再同意が必要）。ただし既に開いているSSEは接続時tokenの失効時刻に終わる（`TOKEN_EXPIRED`）。事前更新はchat等REST呼び出しのため引き続き必要 | 確認済み |
| トークンとDJ | 1 token = 1 DJの同意。path/queryでDJを指定しない | 確認済み |

### tokenレスポンス（発行・更新 共通、snake_case固定）

```json
{
  "access_token": "at_...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "rt_...",
  "scope": "events.chat chat.send"
}
```

OAuth endpointのエラーはRFC 6749 §5.2形式（`error` / `error_description`）。`error_description` は文言が変わるので分岐は `error` で行う。`invalid_scope` はtokenではなく同意ステップで返る。

| error | HTTP | Botの対応 |
|---|---:|---|
| `invalid_request` | 400 | 必須formフィールド不足。修正するまで再送しない |
| `invalid_client` | 401 | クライアント認証失敗 / `client_id` 形式不正 / アプリ停止中。資格情報とアプリ状態を確認 |
| `invalid_grant` | 400 | codeの期限切れ・使用済み・別アプリ発行、redirect_uri不一致、refresh_token無効・失効。codeは同意からやり直し、refresh無効ならDJに再連携を案内 |
| `unsupported_grant_type` | 400 | `authorization_code`/`refresh_token` 以外。実装バグ |

## 3. 権限（scope）（確認済み）

公式の権限一覧は9種類（以前の英語版抜粋の「11種類」は一覧と一致しないため、一覧を正とする）。

| scope | 与えるもの | 対応API |
|---|---|---|
| `live.read` | 配信タイトル・開始/終了時刻・リスナー数・挨拶・カテゴリ・チャット凍結状態 | 現在の配信を取得 `GET /v1/live` |
| `listeners.read` | 部屋にいるリスナーの id・ニックネーム | リスナー一覧を取得 |
| `fans.read` | ギフトが多い順のリスナー順位 | ファンランキングを取得 |
| `events.chat` | リスナーのチャットとDJのクイックメッセージ | イベントストリーム `chat` |
| `events.presence` | リスナーの入場（**Botがマネージャーである必要あり**） | イベントストリーム `presence` |
| `events.like` | リスナーが送ったハート | イベントストリーム `like` |
| `events.donation` | 送り主・合計スプーン・一言 | イベントストリーム `donation` |
| `chat.send` | Botアカウント名での発言 | チャットを送る `POST /v1/live/chat` |
| `dj.follow` | BotアカウントでのDJのフォロー（固定マネージャー指定の前に必要） | DJをフォロー `POST /v1/live/follow` |

用途別の組み合わせ（公式の推奨）:

| Botの機能 | 必要な権限 |
|---|---|
| チャットに反応して返す | `events.chat` `chat.send` |
| 配信の開始・終了を案内する | `live.read` `chat.send` |
| 入場したリスナーを歓迎する | `events.presence` `chat.send` `dj.follow`（＋DJによる固定マネージャー指定） |
| ギフトにお礼を送る | `events.donation` `chat.send` |
| ハートを集計して目標を知らせる | `events.like` `chat.send` |
| リスナーから抽選する | `listeners.read` `chat.send` |
| ファンランキングを読み上げる | `fans.read` `chat.send` |

- 要求できるのはアプリに登録した権限だけ。動作するのはDJが許可した権限だけ。
- 同意画面の `scope` にスペース区切り（URLでは `%20`）で並べる。権限が多いほどDJは許可をためらうため、使うものだけを要求する。
- **同意は積み重なる。** 後から権限を追加するときは、増やした権限で同意画面をもう一度表示すればよく、DJには新しく要求した権限だけが表示される。
- 発行されたトークンのscopeはその時点のDJの同意に基づく。**トークンの更新のたびにscopeを確認する。**
- `events.*` の4権限は1つの接続を共有する（§4.1）。

MVPの要求scope: `events.chat events.like events.donation chat.send`（コマンドとハート・スプーンのお礼。要件R13）。入場ウェルカムを有効にする場合は `events.presence dj.follow` を追加で要求する（同意は積み重なるため、後から追加できる）。

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
| 401 | なし | token無効（失効・破棄・連携解除・アプリ停止すべて含む） | DJに再連携を案内。401を無限リトライしない |
| 403 | なし | `events.*` 権限がひとつもない | 権限を追加して再同意。連携は生きている |
| 403 | `OAPI_MNGR_0209` | 入場できない（DJがBotをブロックした可能性） | 停止しDJに案内 |
| 404 | `OAPI_MNGR_0301` | 配信中でない（状態） | バックオフして再接続 |
| 502 | `OAPI_MNGR_2302` | 配信サーバーエラー | バックオフして再試行 |

> 401と403は別扱い（401=再連携が必要、権限不足の403=再同意だけで済む）。まとめて処理すると正常な連携まで切ってしまう。この表にない共通エラー（400/405/415 `OAPI_MNGR_0103`、呼び出し上限429ボディなし、500 `OAPI_MNGR_1002`）は全エンドポイント共通（§7）。

## 5. マネージャー指定（確認済み）

- 指定はDJがSpoonアプリで行う。APIでは要求できない。
- 推奨は固定マネージャー。BotアカウントがDJを先にフォローしている必要がある（`dj.follow` を得て配信中に `POST /v1/live/follow`、またはBotアカウントでアプリから直接）。
- 同意があっても指定がなければエラーにならず、`presence` だけが届かない（配信サーバーが入場通知をマネージャー等級で発行するため）。
- 固定マネージャーはDJの配信中には変更できない。フォローは配信中しかできない。→ 配信中にフォローし、配信後にDJが指定すると次回配信から有効。
- 配信ごとの指定は最大3名。部屋にいる人だけ指定できる。指定・解除は数秒で反映され、再接続は不要。
- マネージャーになると、マネージャー等級のメッセージも届く。DMはどの場合も届かない。

## 6. チャット送信 `POST /v1/live/chat`（確認済み）

| 項目 | 内容 |
|---|---|
| 認証 | `Authorization: Bearer {access_token}`、`Content-Type: application/json` |
| リクエストボディ | `{"message": string}` のみ。配信はtokenが決めるのでpath/bodyに配信指定なし |
| `message` | 必須・非null。最大200文字。**文字数はUTF-16 code unit基準**（絵文字1つが2文字で数えられることがある）。空・空白のみは `400`（`OAPI_MNGR_0108`）、200超は `400`（`OAPI_MNGR_0109`） |
| 成功レスポンス | **`204` ボディなし**。パース対象がないのでステータスコードだけ見る |
| 配信中でないとき | `404`（`OAPI_MNGR_0301`） |
| 接続不要 | SSEに接続していなくても送れる。部屋にいなければサーバーが先に入場させる |
| 自己echo | 送信メッセージは自分のSSEストリームに戻らない（コマンド再実行事故の防止）。同じ配信の他Botの発言は見える |
| 冪等性・再送安全性 | 公式に冪等キーやidempotency機構の記載なし。成功は204でレスポンスbodyもない。**送信前に `isChatFrozen`・文字数を検証**して失敗を減らし、タイムアウト等での安易な再送は二重投稿になり得る点に注意（再送は429/5xx/タイムアウト時に限定し、クールダウンと組み合わせる） |

- 送信前に [現在の配信を取得 `GET /v1/live`] の `isChatFrozen` を見ると、凍結中の失敗を先に避けられる。
- `429 OAPI_MNGR_0302` はBotが送りすぎた時だけでなく、配信全体のチャット混雑でも発生（上限を配信全体で分け合うため、Botが速度を落としてもすぐには解消しない）。

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

## 9. 同意コールバック（`redirect_uri` に戻る値）（確認済み）

- 成功: `?code=...&state=...`。`code` は60秒以内に交換、`state` は送った値と一致しなければ破棄。
- 失敗: `?error=...&state=...`。

| error | 意味 | 対応 |
|---|---|---|
| `access_denied` | DJが許可しなかった | 連携を止めてDJに案内。再試行は可 |
| `invalid_scope` | アプリが持っていない権限を要求した | scopeをアプリ登録済み権限に絞る。再送しても同じ |

- `client_id`/`redirect_uri` が信頼できないときは **自分のURLに戻さずSpoon側にエラー表示**（`error` も来ない）。送信前にClient IDとredirect URLを確認する。

## 10. 残る未確認事項
1. 秒あたり・1日あたりの具体的な上限値 — 公式も非公開で、実レスポンスの `x-ratelimit-*` ヘッダーで確認する方針（§8）。実装は数値を決め打ちせずヘッダー駆動で速度調整する。

> 旧「未確認」のうち、refresh token寿命（30日）・ローテーション（あり）・scope表現（レスポンス `scope`）・PKCE（未対応）・state（最大512バイト）・同意拒否エラー・chatのrequest(`{"message"}`)/response(`204`)は、2026-10-17に `input/公式ドキュメント/` 全文で確定した（§2・§6・§9）。

## 参照

一次情報は `input/公式ドキュメント/` の公式テキスト全文（2026-10-17時点）。対応する公式URLを併記する。

| ローカル全文 | 公式URL | 確度 |
|---|---|---|
| `input/公式ドキュメント/トークン` | [Tokens](https://developers.spooncast.net/docs/ja/api-reference/tokens) | 確認済み |
| `input/公式ドキュメント/イベントストリーム` | [Event stream](https://developers.spooncast.net/docs/ja/api-reference/live-events) | 確認済み |
| `input/公式ドキュメント/チャットを送る` | [Chat](https://developers.spooncast.net/docs/ja/api-reference/chat) | 確認済み |
| `input/公式ドキュメント/エラーコード` | [Error codes](https://developers.spooncast.net/docs/ja/api-reference/error-codes) | 確認済み |
| `input/公式ドキュメント/権限を選ぶ` | [Choosing permissions](https://developers.spooncast.net/docs/ja/getting-started/choosing-permissions) | 確認済み |
| `input/公式ドキュメント/DJ の同意を得る` | [Get a DJ's consent](https://developers.spooncast.net/docs/ja/getting-started/user-authorization) | 確認済み |
| `input/公式ドキュメント/DJ をフォロー` | [Follow a DJ](https://developers.spooncast.net/docs/ja/api-reference/follow) | 確認済み |
| `input/公式ドキュメント/エンドポイント` | [Endpoints](https://developers.spooncast.net/docs/ja/api-reference/endpoints) | 確認済み |
| `input/公式ドキュメント/現在の配信を取得` | [Read the current broadcast](https://developers.spooncast.net/docs/ja/api-reference/live) | 確認済み |
| `input/公式ドキュメント/リスナー一覧を取得` | [Listeners](https://developers.spooncast.net/docs/ja/api-reference/listeners) | 確認済み |
| `input/公式ドキュメント/ファンランキングを取得` | [Fans](https://developers.spooncast.net/docs/ja/api-reference/fans) | 確認済み |
| `input/公式ドキュメント/アプリ登録` | [App registration](https://developers.spooncast.net/docs/ja/getting-started/app-registration) | 確認済み |
| `input/公式ドキュメント/ボットを作る` | [Build a bot](https://developers.spooncast.net/docs/ja/getting-started/build-a-bot) | 確認済み |

Content was rephrased for compliance with licensing restrictions.
