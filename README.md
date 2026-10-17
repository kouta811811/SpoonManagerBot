# SpoonManagerBot
Spoonにおけるマネージャーのボット機能を作成するためのリポジトリ

- 仕様: [`.kiro/specs/spoon-manager-bot/`](.kiro/specs/spoon-manager-bot/)（要件・設計・タスク）
- Spoon公式APIの要約: [`docs/spoon-api-contract.md`](docs/spoon-api-contract.md)

## DJ向け: ハート・スプーンのお礼文

配信中にDJ本人がチャットで送ると登録できます（リスナーが送っても反応しません）。
登録がない種類にはお礼を送りません。

| コマンド | 動作 |
|---|---|
| `!setheart 文面` | ハートのお礼文を登録・上書き |
| `!setspoon 文面` | スプーンのお礼文を登録・上書き |
| `!setspoon 1000 文面` | 1000スプーン以上の人向けの特別文（ハートも同様。1種類5つまで） |
| `!delheart [下限]` / `!delspoon [下限]` | お礼文を削除 |
| `!msglist` | 登録状況を表示 |

文面で使える変数（文面は150文字まで）:
- `{名前}` … `Aさん`、まとめて送るときは `Aさん、Bさん`（「さん」は自動で付きます）
- `{数}` … ハートの個数・スプーン数（まとめたときは合計）
- `{累計}` … この配信全体の合計

例: `!setheart {名前}ハート{数}個ありがとう💕`

お礼はBot全体で15秒に1通です。その間に届いた人はまとめて1通で送ります。

## 開発

```bash
pip install -e '.[dev]'
ruff check . && ruff format --check .
mypy
pytest
```
