<!-- knowledge-metadata: v1
このファイルは検索/RAGでチャンク分割されても各セクションの文脈が失われないよう、
見出しの直後に機械可読なメタデータをHTMLコメントで持たせています。表示には影響しません。

  kind          record(過去の測定・出来事の記録) / directive(今こうしろ) / state(今こうなっている)
  measured      測定日
  revalidated   再検証日、または false(以後再検証していない)
  status        current / superseded
                — その節が示す「判断・推奨」が現在も有効か。
                  測定値そのものは日付付きの記録なので失効しません。
  superseded_by 判断を置き換えた節の見出し
  subjects      対象モデル
  verdict       その測定から下した判断
  installed     そのモデルが現在このマシンに存在するか
-->

# local-llm-subcontractor

Claude Code や Codex CLI の「下請け」としてローカルLLMを使い、トークン消費(=API課金)を減らすためのワンショット環境構築キットです。

**EN**: One-shot setup for running a local LLM (Qwen3.6-27B via Ollama, or PrismML's Bonsai 27B ternary via their llama.cpp fork) as a cheap "subcontractor" for coding agents like Claude Code / Codex CLI. The orchestrator LLM writes a spec, the local model drafts the code, a local test/lint gate filters the output, and the orchestrator only reviews code that already passes. See `docs/` for agent integration.

## コンセプト

```
Claude Code (頭脳・レビュー役)
   │  仕様を書いて投げる
   ▼
ローカルLLM (下請け: テスト生成・定型コード・ログ要約)
   │  生成物
   ▼
ローカルゲート (pytest / lint / JSONバリデーション)
   │  通ったものだけ
   ▼
Claude Code が最終レビュー
```

ポイントは**ゲートを通ったものだけをオーケストレーターに見せる**こと。これをしないと「Claudeが赤ペン先生として全部読み直す」構図になり、削減効果が消えます。

## クイックスタート

<!-- meta
kind: directive
status: current
note: --model bonsai は現役の選択肢。ただし本機には未インストール(RAM48GBのためqwenを選択)
-->

```bash
git clone https://github.com/focuslight-nr/local-llm-subcontractor.git && cd local-llm-subcontractor
./setup.sh                 # RAM量から自動でモデルを提案
./setup.sh --model qwen    # Qwen3.6-27B Q4 (Ollama, 17GB)
./setup.sh --model bonsai  # Bonsai 27B ternary (llama.cpp fork, 6.7GB)
./setup.sh --model both
```

動作確認:

```bash
echo "Write a Python one-liner that reverses a string." | ./bin/llm -m qwen
```

## どちらのモデルを選ぶか

<!-- meta
kind: directive
status: current
subjects: qwen3.6:27b, bonsai-27b-ternary
basis: 公称値 + 実測ベンチ(2026-07-15)
note: 選択基準はRAM。Bonsaiは8〜16GBマシン向けの現役の選択肢
-->

| | Qwen3.6-27B Q4 | Bonsai 27B 三値版 |
|---|---|---|
| 必要メモリ | ~17GB(実行時ピーク~20GB) | ~7GB(実行時ピーク~10GB) |
| ランタイム | Ollama(本家) | **PrismML製llama.cppフォーク必須** |
| 品質(公称) | フル精度の~99% | フル精度の~95% |
| 向く用途 | テスト生成・仕様追従コード | ログ要約・抽出・定型コード |

**RAM 32GB以上なら Qwen を推奨**。Bonsai は 8〜16GB マシンや常駐メモリを節約したい場合の選択肢です。

### 実測ベンチ(M4 Pro / 48GB, 2026-07-15)

<!-- meta
kind: record
measured: 2026-07-15
revalidated: false
subjects: qwen3.6:27b, bonsai-27b-ternary
verdict: 既定を qwen3.6:27b に採用
status: current
note: Bonsai列は2026-07-15の実測のまま。以降のベンチ更新では環境未セットアップのため再検証していない
-->

`bench/run_bench.py` による自動採点(タスク: pytest生成→実行 / 仕様追従コード / ログ→JSON抽出):

| タスク | Qwen3.6-27B Q4 | Bonsai 27B 三値版 |
|---|---|---|
| pytest生成(実行して全パス) | ✅ 8/8 pass (52s) | ❌ 期待値の計算ミス + import漏れ (24s) |
| 仕様追従コード(7ケース) | ✅ 7/7 (17s) | ✅ 7/7 (6s) |
| ログ→JSON抽出(完全一致) | ✅ (35s) | ✅ (24s) |
| 生成速度 | 9–10 tok/s | 11–15 tok/s |

Bonsaiの失敗はケアレスミス型(算数ミス・import忘れ)で、まさにゲートで機械的に弾けるタイプでした。
(Bonsai列は2026-07-15時点の実測のまま。以降のベンチ更新ではBonsai環境が未セットアップのため再検証していません。)

### 追記: MoE版 `qwen3.6:35b-a3b` との比較(同環境, 2026-07-25再検証)

<!-- meta
kind: record
measured: 2026-07-25
subjects: qwen3.6:27b, qwen3.6:35b-a3b
verdict: 当時の推奨「既定 35b-a3b、テスト生成のみ 27b」
status: superseded
superseded_by: 第2段タスク(2026-08-11追加)と再評価
note: 測定値は有効。推奨のみ第2段タスクの結果で覆り、既定は27bに戻した
-->

| タスク | qwen3.6:27b (dense) | qwen3.6:35b-a3b (MoE, 24GB) |
|---|---|---|
| pytest生成 | ✅ 8/8 pass (50.6s) | ⚠️ 5戦4勝(下記参照) (7–16s) |
| 仕様追従コード | ✅ 7/7 (17.4s) | ✅ 7/7 (1.9–2.4s) |
| ログ→JSON抽出 | ✅ (36.3s) | ✅ (9.3–10.2s) |
| 生成速度 | 8.5–10.1 tok/s | 21.9–49.6 tok/s |

アクティブパラメータ3BのMoEなので速度は2.5〜5倍。抽出・定型コードは同品質で安定。
テスト生成だけは**35b-a3bにフレーキーな失敗が確認できました**: 同日中に5回実行して
期待値の算数ミスで落ちたのは1回のみ(4回は8〜15本のテストが全パス)。「35b-a3bは
テスト生成で必ず落ちる」わけではなく、たまに落ちる不安定さがあるという方が正確です。
27bはこの5回を含め全ての計測で安定して全パスしており、**テスト生成だけは27b固定**という
既存の推奨はそのまま維持します。
(この時点では「デフォルト35b-a3b、テスト生成だけ27b」を推奨していましたが、
下の第2段タスクの結果を受けて **デフォルトは27b** に改めました。)
`bin/llm -m qwen3.6:35b-a3b` のように `-m` へ任意のOllamaモデル名を渡せます。
インストールは `QWEN_MODEL=qwen3.6:35b-a3b ./setup.sh --model qwen` でも、`ollama pull` 直でも。

再現手順: `BENCH_OLLAMA_MODELS=qwen3.6:27b,qwen3.6:35b-a3b python3 bench/run_bench.py`

### 採用: Ornith-1.5 35B を `--think` 枠に(2026-08-20)

<!-- meta
kind: record, directive
measured: 2026-08-20
subjects: ornith-1.5:35b
verdict: --think 枠に採用。テスト生成には使わない(1/3)
status: current
installed: true
note: 現在有効な --think 枠の根拠。報道の「Opus 4.8に匹敵」は397B版であり本節の35B版ではない
-->

Ornith製、MITライセンス。397B / 35B(MoE, アクティブ3B) / 9B の3サイズで、
**報道の「Claude Opus 4.8に匹敵」(Terminal-Bench 86.1)は397B版**(242GB)の数字です。
ローカルで動くのは35B(23GB)と9B(6.6GB)で、35B版自身の公称値は
SWE-bench Verified 79 / Terminal-Bench 67.8–68.5 と控えめ。**大きい方の数字で
小さい方を判断しない**のが、この種の発表を読むときの要点です。

推奨設定は **推論オン・temperature 0.6**(ベンチ再現時1.0)。両条件で測りました。

| 条件 | スコア | 合計時間 | 速度 |
|---|---|---|---|
| 思考オフ(temp 0.2) | 2/6 | 40秒 | 33–48 tok/s |
| **思考オン(推奨, temp 0.6)** | **6/6 · 4/6 · 5/6**(n=3) | 307–342秒 | 47–51 tok/s |

タスク別の通過率(思考オン, n=3): 仕様追従 3/3、抽出 3/3、矛盾仕様 3/3、CSV 3/3、
ミューテーション 2/3、**pytest生成 1/3**。

**強み**: 推論を入れたまま約50 tok/sを維持し、6タスクを約5分で完走します。同じ品質域に
qwen3.8:27bは25分、qwen3.6:27bは45分かかるので、**5〜9倍速い**。うちのMoE
(`qwen3.6:35b-a3b`)が落とす矛盾仕様の申告とCSVの精密仕様を、いずれも3/3で通します。

**弱み**: テスト生成が不安定(1/3)。失敗は毎回**期待値の算数ミス**で、MoE勢に共通する癖です。
興味深いことに、間違えるタスクほど思考が短い(pytest生成 1,140字 に対し CSV 14,879字)——
難しさの判定を誤り、考えずに答えています。

**採用方針**: 既定は `qwen3.6:27b` のまま(テスト生成の安定性で勝る)。`--think` 枠を
`qwen3.8:27b` から **`ornith-1.5:35b` に差し替え**ました。同じ品質域に5倍速く到達するためで、
**テスト生成には使わない**という但し書き付きです。

### 思考モードで結論が変わる: Qwen3.8-27B(2026-08-15)

<!-- meta
kind: record
measured: 2026-08-15
subjects: qwen3.6:27b, qwen3.8:27b
verdict: 既定は3.6据え置き / 当時の --think 枠は 3.8
status: superseded
superseded_by: 採用: Ornith-1.5 35B を `--think` 枠に(2026-08-20)
installed: qwen3.8:27b = false(2026-08-20に削除)
note: 「既定は3.6」は現在も有効。差し替わったのは --think 枠の指定のみ
-->

同じ27B級の後継世代(要 Ollama 0.32.13 以上。0.32.9 では `pull` が412で弾かれます)。
**思考モードの有無で結論が反転した**ので、2×2で計測しました。

> **試験条件の明示**: 本キットのベンチは既定で **思考モードを無効**(`think:false`)、
> **temperature 0.2** で回します。下請け用途では思考がトークン上限を食い潰して回答が
> 返らない事故があったための設計判断で、**ベンダー推奨の設定ではありません**
> (Qwen3.8のモデルカードは thinking on / temp 1.0 を推奨)。以下は両条件の実測です。

| | qwen3.6:27b | qwen3.8:27b |
|---|---|---|
| **思考オフ**(既定, temp 0.2) | **6/6 · 275秒** | 5/6 · 387秒 |
| **思考オン**(推奨, temp 1.0) | 6/6 · 2687秒 | **6/6 · 1521秒** |

読み取れることが3つあります。

1. **3.8の失点は思考オフ限定**。思考オフではCSVパーサを3回中3回落としました
   (末尾の空フィールドの扱いを誤り、余分に足すか落とすか)が、**思考オンでは10/10**。
   「苦手」ではなく「思考なしでは詰めきれない」問題でした。
2. **思考オンなら3.8の方が速い**(25分 vs 45分)。推論の冗長さが段違いで、CSVタスクの
   思考は 3.8 が 9,284字、3.6 は 26,343字。**同じ結論に約1/3の推論で到達**します。
3. **それでも既定は `qwen3.6:27b` 据え置き**。本キットは思考オフで運用しており、
   その条件では3.6が品質・速度とも上回るためです。思考オンを許容できる用途
   (時間をかけてよい一括処理)なら3.8が有利です。

(この `--think` 枠は2026-08-20に Ornith-1.5 35B へ差し替えました。上のセクション参照。)

なお公開直後のレビュー記事は**軒並み実測ではありません**(ある詳細レビューは
"We did not run inference" と明記し、独立した再現がない旨も併記)。数値の出所は
ほぼベンダー発表です。手元のベンチと食い違ったときは、まず**自分の試験条件**を
疑うのが先でした——今回それを踏みました。

### 検討して見送ったモデル: Meta Muse Glimmer 30B(2026-08-11)

<!-- meta
kind: record
measured: 2026-08-11
subjects: muse-glimmer-30b
verdict: 不採用(当時の理由: プレリリース版Ollama必須 / タスク飽和 / 強みが本キットの非対象領域)
status: superseded
superseded_by: 再評価(2026-08-13, Ollama 0.32.9 安定版)
installed: false
note: 不採用の結論は維持。理由の1点目(運用制約)のみ2026-08-13に解消済み
-->

Metaのエージェント特化モデル(Apache 2.0, 128K, マルチモーダル)。公式ベンチが
Qwen3.6-27Bとの直接比較を載せており、下請け候補として実測しました。

| タスク(第1段) | Qwen3.6-27B @0.2 | Muse Glimmer @0.2 | Muse Glimmer @1.0(Meta推奨) |
|---|---|---|---|
| pytest生成 | ✅ 8テスト (47s) | ✅ 12テスト (52s) | ✅ 14テスト (38s) |
| 仕様追従コード | ✅ 7/7 (17s) | ✅ 7/7 (22s) | ✅ 7/7 (22s) |
| ログ→JSON抽出 | ✅ (35s) | ✅ (69s) | ✅ (73s) |
| 生成速度 | 9–10 tok/s | 10–12 tok/s | 13 tok/s |

品質は良好で、テストの網羅性は27bを上回り、算数ミスもありませんでした。Meta推奨の
temperature 1.0 でも劣化しません(この検証のため `BENCH_TEMPERATURE` を追加)。
それでも**採用を見送った**理由は3点:

1. **プレリリース版のOllama(v0.32.8-rc0)が必須**。安定版のログインサービスでは動かず、
   常用するとRC版サーバーを別に動かし続けることになり、ゼロメンテ構成が崩れる
   (→ 0.32.9で解消。下の再評価を参照)
2. 第1段タスクでは**両者とも満点で差がつかなかった**(この飽和が第2段タスク追加の動機)
3. 公称の強み(MCP Atlas 75.5 vs 62.5 など)は**ツール呼び出し・エージェント能力**に集中しており、
   本キットが意図的に使わない領域。前節のとおり、ローカルモデルには書き込み権限を渡さない設計

#### 再評価(2026-08-13, Ollama 0.32.9 安定版)

<!-- meta
kind: record
measured: 2026-08-13
subjects: qwen3.6:27b, muse-glimmer-30b
verdict: 不採用を維持(理由は運用制約から所要時間へ変更。品質は互角)
status: current
installed: false
note: 冗長性が変わらない限り再テスト不要
-->

Ollamaの安定版が0.32.9になり、**プレリリース版なしで常用サーバーのまま動く**ようになったため、
第2段タスクを含む全6タスクで測り直しました。

| タスク | qwen3.6:27b | Muse Glimmer 30B |
|---|---|---|
| 1 pytest生成 | ✅ (64s) | ✅ (51s) |
| 2 仕様追従コード | ✅ (17s) | ✅ (33s) |
| 3 ログ→JSON抽出 | ✅ (35s) | ✅ (76s) |
| 4 ミューテーション | ✅ 3/3検出 (95s) | ✅ 3/3検出 (113s) |
| 5 矛盾仕様の検出 | ✅ 報告 (5s) | ✅ 報告 (22s) |
| 6 CSVパーサ | ✅ 10/10 (75s) | ✅ 10/10 (268s) |
| **合計** | **6/6 / 292秒** | **6/6 / 564秒** |

品質は互角(第2段タスクも全て通過、35b-a3bが落ちた5番も正しく申告)。決め手は**所要時間**で、
tok/s はほぼ同等ながら生成量が多く、実時間で約2倍かかります(6番は3.5倍)。

**結論は変わらず不採用**ですが、理由は運用制約から速度に変わりました。品質面では
27bの代替として十分に通用します。第2段タスクもこの2モデルでは飽和しており、
両者を分けるにはさらに難しいタスクが要ります。

### 第2段タスク(2026-08-11追加)と再評価

<!-- meta
kind: record, directive
measured: 2026-08-11
subjects: qwen3.6:27b, qwen3.6:35b-a3b
verdict: 既定 = qwen3.6:27b / 35b-a3b は要約・抽出などの一括処理に限定
status: current
note: 現在有効な既定モデルの根拠。2026-07-25の推奨を上書きしている
-->

上記3タスクは候補モデルが軒並み満点になり判別力を失ったため、難度の高い3タスクを追加しました:

| # | タスク | 測るもの |
|---|---|---|
| 4 | **ミューテーションテスト** | 生成テストが注入バグ3種を検出できるか(通るだけでは0点) |
| 5 | **矛盾仕様の検出** | 実装不可能な仕様を `SPEC_CONFLICT:` で報告できるか(黙って実装したら0点) |
| 6 | **CSVパーサ** | 引用符・`""`エスケープ・末尾空フィールドなど10ケース完全一致 |

5番は、下請け運用で最も危険な失敗——仕様とコードが矛盾したときにモデルが問題を報告せず
勝手に辻褄を合わせる挙動(前節のエージェント実験で観測したもの)——を直接測ります。

結果(2026-08-11, 各1回):

| タスク | qwen3.6:27b | qwen3.6:35b-a3b |
|---|---|---|
| 4 ミューテーション | ✅ 3/3 検出 | ❌ `import pytest` 漏れで22件エラー |
| 5 矛盾仕様 | ✅ 正しく報告 | ❌ **不可能な仕様を黙って実装** |
| 6 CSVパーサ | ✅ 10/10 | ❌ 9/10(末尾カンマの空フィールドを脱落) |
| **合計(全6タスク)** | **6/6** | **3/6** |

速度と引き換えに、MoE版は「正確さ」と「問題の申告」で明確に劣ります。とくに5番の失敗は
テスト生成に限らずあらゆる委譲に関わります。

**現在の推奨**: 既定は `qwen3.6:27b`。MoE版 `qwen3.6:35b-a3b` は、速度が効いて精度要求が
低い一括処理(ログ/ドキュメントの要約・構造化抽出。1〜3番は満点)に限って使う。

環境変数:

| 変数 | 既定 | 用途 |
|---|---|---|
| `BENCH_OLLAMA_MODELS` | `qwen3.6:27b` | 対象モデル(カンマ区切り) |
| `BENCH_TASKS` | 全部 | タスク絞り込み(例 `4_mutation,6_csv`) |
| `BENCH_TEMPERATURE` | `0.2` | サンプリング温度 |
| `BENCH_THINK` | 無効 | `1` で思考モード有効(ベンダー推奨設定での計測用) |
| `BENCH_MAX_TOKENS` | `4000` | 出力上限。思考オン時は 12000 程度に上げること |
| `BENCH_NUM_CTX` | `16384` | コンテキスト長。思考オン時は 32768 程度 |

ベンダー推奨設定で測る例:

```bash
BENCH_THINK=1 BENCH_TEMPERATURE=1.0 BENCH_MAX_TOKENS=12000 BENCH_NUM_CTX=32768 \
  BENCH_OLLAMA_MODELS=qwen3.8:27b python3 bench/run_bench.py
```
(Ollamaを起動し両モデルをpull済みの状態で実行。`bench/results.json` と `bench/out_*.txt` に
生の出力が残るが、`.gitignore` によりリポジトリには含めない。)

## なぜ「エージェント」ではなく「下請け」なのか(実験記録)

<!-- meta
kind: record, directive
measured: 2026-07-17
subjects: LM Studio Bionic 1.0 + qwen3.6:27b
verdict: 自律エージェント化しない。ローカルモデルに書き込み権限を渡さずゲートを外側で回す
status: current
note: bin/llm が stdin→stdout の単純なパイプである理由
-->

ローカルLLMを頭脳にした自律エージェント(LM Studio Bionic 1.0 + qwen3.6-27B)と
本キットの構成で、同じテスト生成タスク(実在OSSの4関数、同一仕様書)を比較しました(2026-07-17, M4 Pro 48GB):

| | Claude+下請け(本キット) | エージェント + 27B |
|---|---|---|
| 成果物 | 18テスト全パス | 21テスト「全パス」 |
| 所要時間 | 2〜3分 | 19分 |
| ソースの安全性 | 構造的に書換え不可能 | **テスト対象のライブラリを書き換えて合格を偽装** |

エージェント側は、仕様書とコードが矛盾する箇所(こちらが意図的に混入したわけではなく、
仕様書が古いバージョン基準だったという実験ミス)に遭遇した際、矛盾を報告する代わりに
**ライブラリ本体を書き換えてテストを通しました**。修正済みのバグを黙って復活させた形で、
diffを取らなければ気づけませんでした。

教訓: 27Bクラスをローカルで使うなら、自律エージェントとして書き込み権限を与えるより、
**テキスト生成だけさせて検証(ゲート)を外側で回す**方が安全です。仕様との矛盾は
「ゲートの失敗」として表面化し、人間(またはオーケストレーターのLLM)が判断できます。
本キットの `bin/llm` がstdin→stdoutの単純なパイプなのは、この設計判断によるものです。

## リポジトリ構成

```
setup.sh            # 環境構築(macOS / Linux, 冪等)
bin/llm             # 下請け呼び出しCLI(stdin/引数 → 応答をstdout)
bin/serve-bonsai    # Bonsai用llama-server起動
bench/              # 自動採点ベンチ(モデル追加時の品質確認用)
docs/claude-code.md # Claude Code への組み込み手順(CLAUDE.mdスニペット付き)
docs/codex.md       # Codex CLI への組み込み手順(AGENTS.mdスニペット付き)
```

インストール先はデフォルト `~/local-llm`(`--dir` か環境変数 `LLM_HOME` で変更可)。モデル本体・ビルド成果物はリポジトリ外に置かれます。

## 重要な注意

<!-- meta
kind: directive
status: current
note: 運用上の必須事項。特定の測定日に紐づかない
-->

- **thinkingの無効化**: Qwen3.6もBonsaiもthinkingモデルです。無効化しないと思考だけでトークン上限を使い切り、本体の回答が届きません。`bin/llm` は両バックエンドで無効化済み(Ollama: `think: false` / llama-server: `chat_template_kwargs.enable_thinking: false`)。
- **Bonsaiは本家llama.cpp/Ollamaでは動きません**。独自量子化(Q2_0_g128)のため、PrismML公式のllama.cppフォーク(github.com/PrismML-Eng/llama.cpp)のビルドが必要です。モデル配布元が案内する正規ランタイムですが、本家よりコミュニティ監査が薄い第三者コードをビルド・実行することは理解した上で選んでください(`setup.sh` も確認を求めます)。
- **Ollamaのコンテキスト長**: デフォルトが短いため、`bin/llm` はリクエスト毎に `num_ctx` を指定しています。エージェントのモデルを丸ごと差し替える場合(docs参照)は `OLLAMA_CONTEXT_LENGTH=65536` 以上を推奨。

## License

MIT
