# 環境の作り方

以下はLinuxで動作確認済みです。Windowsでも同様ですが、環境変数の設定構文の違いは適宜環境に合わせてください。

## 1. 環境変数

サーバー側の環境変数は `server/.env` にまとめて管理するのを推奨します（秘密情報をシェルスクリプトに直書きしない）。

ひな形は [server/.env.example](server/.env.example) にあります（このファイルはコミット対象、`.env` は非コミット）。

補足: このリポジトリの起動スクリプトは Python 仮想環境を `server/.venv` に統一して使います。

1. ひな形をコピー

```bash
cd server
cp .env.example .env
```

2. `server/.env` を編集して値を設定

設定する環境変数は以下です：

```bash
export AZURE_COMMUNICATION_CONNECTION_STRING=<ACSの接続文字列>
export AZURE_OPENAI_ENDPOINT=<AOAIのEndpoint【例】wss://xxx.openai.azure.com>
export AZURE_OPENAI_DEPLOYMENT=<AOAIのデプロイメント名【例】gpt-realtime>
export AZURE_OPENAI_API_KEY=<AOAIのAPIキー。Entra ID認証実行時には不要>
export AOAI_VOICE=<応答に使う音声。現在sageを指定>

# （任意）AOAI の system prompt / instructions（長い場合はファイル推奨）
# export AOAI_INSTRUCTIONS_FILE=./prompts/aoai_instructions.txt
# export AOAI_INSTRUCTIONS="あなたは..."
export CALLBACK_URI_HOST=<サーバーのパブリックURL【例】https://my-server.com>

# （任意）Microsoft Foundry Agent（Web grounding）
# 既存の Agent を 1つ用意し、Web grounding を有効化した上で、その Agent ID を指定します。
export AZURE_AI_PROJECT_ENDPOINT=<Foundry project endpoint>
export AZURE_AI_AGENT_ID=<Foundry agent id>

# （任意）Agent 呼び出し設定
# Agent の実行が失敗/タイムアウトした場合のみ、一般知識で回答するフォールバックに切り替えます。
export MEDIA_WS_AGENT_TIMEOUT_MS=2000
export MEDIA_WS_AGENT_FALLBACK_PREFIX="今は検索できないので一般知識で答えます"

# （任意）音質改善: リサンプリング品質 (soxr)
export MEDIA_WS_SOXR_QUALITY=VHQ
```

## 必要な設定（Azure / Foundry / Ingress）

このリポジトリは「ACS Call Automation（IncomingCall）+ Media Streaming（WebSocket）+ AOAI Realtime（音声）+（任意）Foundry Agent（Web grounding）」の構成です。
最短で詰まりやすいポイントを含めて、必要な設定を一覧化します。

環境変数のひな形は [server/.env.example](server/.env.example)、実際に編集するのは `server/.env` です。

### 1) 公開 URL（HTTPS）と WebSocket（WSS）

- `CALLBACK_URI_HOST` は **https の公開 URL**（末尾スラッシュ無し）にします。
- ACS が到達する必要があるパス:
  - `POST /api/incomingCall`（Event Grid → IncomingCall）
  - `POST /api/callbacks`（Call Automation callback）
  - `wss://.../ws/media`（Media Streaming）
- 「HTTP は開けるが WebSocket が通らない」ケースがあるので、必ず WSS を疎通確認してください（下のテスト方法参照）。

### 2) Azure Communication Services（ACS）

- ACS リソースを用意し、接続文字列を `AZURE_COMMUNICATION_CONNECTION_STRING` に設定します。
- Call Automation を使うため、サーバーから見える `CALLBACK_URI_HOST` が必須です。

### 3) Event Grid（IncomingCall をサーバーに届ける）

- ACS の IncomingCall をサーバーに届けるため、Event Grid のサブスクリプションを作成し、エンドポイントを
  - `https://<public-host>/api/incomingCall`
  にします。
- サブスクリプション作成時に `SubscriptionValidationEvent` が送信されます。本サーバーは `validationResponse` を返して検証に応答します。

### 4) Media Streaming（/ws/media）

- `answer_call` 時に Media Streaming の WebSocket URL を `wss://<public-host>/ws/media` に指定します。
- 統合ゲートウェイ（推奨）を使う場合、公開ポートは 8000 だけで OK です（HTTP と `/ws/media` を同一アプリで処理）。

### 5) Teams interop（Teams user を Entra objectId で指定）

- Teams 宛発信（`POST /api/call/start` の `targetKind:"teams"`）は、Teams ユーザーの **Entra ID object ID** を `entraObjectId` に指定します。
- ACS 側の Teams interop 設定（テナント前提/許可/ポリシー等）が必要です。

## Teams interop の構成（このリポジトリでやっていること）

このリポジトリは「ACS Call Automation が通話を制御し、Media Streaming で音声をサーバに流し、AOAI Realtime が音声応答を生成する」構成です。
Teams interop を使う場合、Teams 側の相手は **Entra ID object ID** で指定し、ACS SDK の Teams Identifier に変換して発信します。

### 全体フロー

1) （Outbound）サーバから Teams ユーザーへ発信

- クライアントが `POST /api/call/start` を呼ぶ
- `targetKind:"teams"` + `entraObjectId:<Entra object id>` を受け取り、サーバが `MicrosoftTeamsUserIdentifier(entraObjectId)` を使って `create_call`
- 相手が応答すると、サーバは Call Automation の callback（`/api/callbacks`）を受けつつ、Media Streaming を `wss://<public-host>/ws/media` に流す

2) （Inbound）Teams → サーバ（IncomingCall）

- Teams からの着信は Event Grid を経由して `POST /api/incomingCall` に届く
- サーバは `answer_call` で応答し、Media Streaming を開始

3) 音声対話

- `wss://.../ws/media` で受けた音声をサーバが AOAI Realtime に中継
- （任意）転記テキストを Foundry Agent（Web grounding）に渡し、結果テキストを AOAI Realtime に「読み上げ指示」として送る

### 前提・注意点（Teams interop）

- `entraObjectId` は **同一テナントの Teams ユーザー**の Entra ID object ID を想定しています。
- Teams interop は Azure 側（ACS リソース）および Microsoft 365 側（テナント/ポリシー）での事前設定が必要です。
  - ここは環境依存のため、このリポジトリは「必要な識別子を渡せる API と、Call Automation / Media Streaming の受け口」を提供します。
- 設定が不十分な場合の典型症状:
  - `POST /api/call/start` は `ok:true` を返すが、相手が鳴らない/すぐ切れる
  - IncomingCall が届かない（Event Grid ルーティング/validation/公開 URL の問題）
  - `MediaStreamingFailed`（WSS 到達性の問題）

### Azure 側チェックリスト（Teams interop を動かすために最低限見る場所）

- ACS リソース
  - Teams interop が有効になっていること（ACS 側で Teams との相互運用を許可する設定が必要です）
  - Call Automation が使える状態であること（接続文字列/権限）

- Event Grid（IncomingCall をサーバへ）
  - Event subscription のエンドポイントが `https://<public-host>/api/incomingCall` になっている
  - SubscriptionValidation が成功する（本サーバは `validationResponse` を返します）

- Ingress / ネットワーク
  - `CALLBACK_URI_HOST` がインターネットから到達できる **https** になっている
  - `POST /api/callbacks` が外部から到達できる
  - `wss://<public-host>/ws/media` が外部から到達できる（WebSocket upgrade が通る）

- （任意）Foundry Agent（Web grounding）
  - Foundry を使う場合、実行環境で `DefaultAzureCredential` が通る（ローカル `az login` / ACA Managed Identity 等）

### すぐできる確認（順番が大事）

詳細なコマンドは「[テスト方法（コピペで確認）](#テスト方法コピペで確認)」を参照してください。

1) `CALLBACK_URI_HOST` で `wss://.../ws/media` の疎通が取れる（`npx wscat`）
2) `POST /api/incomingCall` の SubscriptionValidation が返る（curl で擬似投入）
3) `POST /api/call/start`（teams）で発信できる（相手が鳴る/応答後に `/ws/media` へ流れる）

### 6) AOAI Realtime（音声）

- `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_API_KEY` / `AOAI_VOICE` を設定します。
- `AZURE_OPENAI_ENDPOINT` は `https://...` でも `wss://...` でも構いません（コード側で処理します）。

### 7) （任意）Foundry Agent（Web grounding）

- Foundry 側で Web grounding を有効にした Agent を用意し、以下を設定します。
  - `AZURE_AI_PROJECT_ENDPOINT` + `AZURE_AI_AGENT_ID`（または `AZURE_FOUNDRY_PROJECT_ENDPOINT` + `AZURE_FOUNDRY_AGENT_ID`）
- 認証は `DefaultAzureCredential` を使用します。
  - ローカル: `az login`
  - ACA/コンテナ: Managed Identity など
- Agent が失敗/タイムアウト/空結果のときのみ、`MEDIA_WS_AGENT_FALLBACK_PREFIX` を先頭に付けて一般知識回答にフォールバックします。

注意:

- `.env` はコミットしないでください（このリポジトリでは `.gitignore` で除外しています）。
- もし過去にキー/接続文字列をコミットしてしまっている場合は、ACS/AOAI 側でキーをローテーションしてください。

設定確認:

- サーバー起動後に `http://localhost:8000/api/health` を開くと、環境変数が読み込めているか（秘密情報は表示しない）を確認できます。

### ローカルPCで動かす場合の注意（重要）

ACS Call Automation は、

- Callback URL（`/api/callbacks`）
- Media Streaming の WebSocket（`/ws/media`）

に **Azure 側から到達できる必要**があります。ローカルPCでそのまま `http://localhost:8000` を指定しても、ACS からは到達できません。

そのためローカル検証では、`ngrok` や `cloudflared` 、`devtunnel` などで **https:// の公開URL**を作り、`CALLBACK_URI_HOST` に設定してください。

例（cloudflared）:

```bash
# 8000 を外部公開 (https://xxxx.trycloudflare.com が発行されます)
cloudflared tunnel --url http://localhost:8000

# 生成された https://xxxx.trycloudflare.com を server/.env の CALLBACK_URI_HOST に設定
```

例（Microsoft Dev Tunnels / devtunnel CLI）:

Dev Tunnels は Microsoft の開発用トンネルで、ローカルの HTTP/WS を外部から `https://...devtunnels.ms` / `wss://...devtunnels.ms` 経由で到達させることができます。

1. devtunnel CLI をインストール（Linux）

> [開発トンネルを作成してホストする](https://learn.microsoft.com/ja-jp/azure/developer/dev-tunnels/get-started) を参照

```bash
curl -sL https://aka.ms/DevTunnelCliInstall | bash
```

2. ログイン

```bash
devtunnel user login
```

3. トンネル作成（ACS から到達できる必要があるので匿名アクセス許可が必要になるケースが多い）

```bash
devtunnel create --allow-anonymous
# port create 自体には "public" などのフラグはありません（CLIの access 管理で許可します）
devtunnel port create --port-number 8000 --protocol http

# 匿名アクセスを明示的に許可（これを入れないと 401 になることがあります）
devtunnel access create --anonymous --port-number 8000

devtunnel host
```

補足: すでに作ったトンネルで 401 が返る場合は、以下で現在のアクセス設定を確認できます。

```bash
devtunnel access list
```

4. `devtunnel host` の出力に表示される `https://<tunnelId>-8000.<region>.devtunnels.ms` のような URL を、`server/.env` の `CALLBACK_URI_HOST` に設定

初回は「Connect via browser」URL をブラウザで開いて **Continue** を押して有効化が必要な場合があります（エラー画面が出ても無視して OK です）。

### WebSocket 到達性チェック（重要）

`CallConnected` 直後に `Microsoft.Communication.MediaStreamingFailed` が出て `initialWebSocketConnectionFailed` になる場合、
ほぼ確実に **ACS から `wss://.../ws/media` に接続できていません**。

まずは「外部から見て WebSocket が通るか」を確認してください（HTTP/HTTPS が開けても WebSocket が通らないケースがあります）。

- Node で簡易チェック（推奨）

```bash
npx wscat -c wss://<public-host>/ws/media
```

接続が維持できれば OK です（この `/ws/media` は疎通確認用に `pong` を返しません）。

- Python で簡易チェック（リポジトリ内スクリプト）

```bash
uv run -- python server/scripts/ws_probe.py --url https://<public-host> --path /ws/media
```

これがタイムアウトする場合、ACS も同様に失敗します。その場合は以下を見直してください。

- devtunnel の匿名アクセス許可（`devtunnel access create --anonymous --port-number 8000`）
- `devtunnel host` が動き続けているか（アイドルで落ちていないか）
- 別のトンネル（`ngrok http 8000` / `cloudflared tunnel --url http://localhost:8000`）に切り替えて再検証

#### （おすすめ）1つの公開ポートだけで動かす（統合ゲートウェイ）

このリポジトリには **「公開ポートは 8000 だけ」** にして、

- HTTP（`/api/...`）は内部 FastAPI へ
- Media Streaming の WebSocket（`/ws/media`）はゲートウェイ自身で処理

ルーティングする **統合ゲートウェイ**が入っています。

1. 統合ゲートウェイを起動

```bash
./startup_server.sh

```

これで **トンネル公開は 8000 だけ**で済みます。

補足: 依存関係は `uv` がある場合は `uv sync --frozen`、無い場合は `pip install -r server/requirements.txt` で導入します（起動スクリプト内で自動判定）。

補足（uv.lock について）:

- `uv.lock` がある場合は再現性のため `uv sync --frozen`
- `uv.lock` が無い場合は `uv sync`

を実行します（`startup_server.sh` が自動で分岐します）。

## Troubleshooting

- Event Grid のサブスクリプション作成で validation が失敗する
  - `POST /api/incomingCall` が外部から到達できることを確認してください（`CALLBACK_URI_HOST` が公開 URL になっているか）。
  - このサーバーは `Microsoft.EventGrid.SubscriptionValidationEvent` を受けると `{"validationResponse":"..."}` を返します。
  - ローカルでの簡易確認:

    ```bash
    curl -sS -X POST http://localhost:8000/api/incomingCall \
      -H 'content-type: application/json' \
      -d '[{"eventType":"Microsoft.EventGrid.SubscriptionValidationEvent","data":{"validationCode":"abc123"}}]'
    ```

- `Microsoft.Communication.MediaStreamingFailed` / `initialWebSocketConnectionFailed` が出る
  - ACS から `wss://<public-host>/ws/media` に接続できていません。
  - まず `npx wscat -c wss://<public-host>/ws/media` で外部到達性を確認してください。

- Foundry Agent が使われずフォールバックばかりになる
  - `AZURE_AI_PROJECT_ENDPOINT` と `AZURE_AI_AGENT_ID`（または `AZURE_FOUNDRY_*`）が設定されているか確認してください。
  - 認証は `DefaultAzureCredential` を使うため、ローカルなら `az login`、ACA/コンテナなら Managed Identity 等の資格情報が必要です。
  - Agent が失敗/タイムアウトした場合のみ `MEDIA_WS_AGENT_FALLBACK_PREFIX` を付けて一般知識回答に切り替わります。

- Teams interop の `POST /api/call/start` が失敗する
  - `entraObjectId` は Teams ユーザーの Entra ID object ID である必要があります。
  - ACS リソース側の Teams interop 設定（テナント前提/許可）を確認してください。

## テスト方法（コピペで確認）

Teams interop を含む疎通確認は「[すぐできる確認（順番が大事）](#すぐできる確認順番が大事)」の順で進めるのがおすすめです。

### 1) ローカル起動（最小スモーク）

```bash
./startup_server.sh
```

別ターミナルで:

```bash
curl -sS http://localhost:8000/api/health | jq
```

### 2) Event Grid SubscriptionValidation の動作確認

起動中に実行:

```bash
curl -sS -X POST http://localhost:8000/api/incomingCall \
  -H 'content-type: application/json' \
  -d '[{"eventType":"Microsoft.EventGrid.SubscriptionValidationEvent","data":{"validationCode":"abc123"}}]'
```

期待結果:

- `{"validationResponse":"abc123"}`

### 3) WebSocket 到達性チェック（最重要）

ACS は `wss://<public-host>/ws/media` に接続してくるため、公開 URL で確認します。

```bash
npx wscat -c wss://<public-host>/ws/media
```

接続が維持できれば OK です（`/ws/media` は疎通確認用に `pong` を返しません）。

### 4) Teams interop 発信（Entra objectId 固定）

起動中に実行:

```bash
curl -sS -X POST http://localhost:8000/api/call/start \
  -H 'content-type: application/json' \
  -d '{
    "targetKind":"teams",
    "entraObjectId":"00000000-0000-0000-0000-000000000000",
    "sourceDisplayName":"Realtime Server"
  }'
```

期待結果:

- `{"ok":true, ...}`（※実際の通話成立は Azure 側の Teams interop 設定に依存します）

### 5) Foundry Agent（Web grounding）連携の確認

前提:

- `AZURE_AI_PROJECT_ENDPOINT` + `AZURE_AI_AGENT_ID`（または `AZURE_FOUNDRY_*`）を設定
- 実行環境で `DefaultAzureCredential` が通ること（ローカルなら `az login`、ACA なら Managed Identity 等）

確認観点:

- Agent が成功した場合: その結果テキストを Realtime が読み上げ
- Agent が失敗/タイムアウト/空結果の場合のみ: `MEDIA_WS_AGENT_FALLBACK_PREFIX` を先頭に付けた一般知識回答にフォールバック

### 6) AOAI Realtime 再接続（通話維持）の確認

確認観点:

- Realtime 側が一時的に落ちても、ACS Media WS（通話）は切らずに再接続を継続
- 復旧後に発話が再開される

## 2. ACSのUserの作成

[エンド ユーザーのアクセス トークンを作成および管理する](https://learn.microsoft.com/ja-jp/azure/communication-services/quickstarts/identity/access-tokens?tabs=linux&pivots=platform-azcli)

事前に環境変数でACS接続文字列を設定しておく。

1. ID の作成

```bash
# 接続文字列を設定済みなら、 --connection-string 以後は不要
az communication identity user create --connection-string "<yourConnectionString>"
```

応答は以下のよう。

```json
{
  "properties": {
    "id": "8:acs:5f6d34d4-dad2-48b1-bad6-30e9cc12bcc8_0000002c-0e29-50fd-72e4-6f8ded7cbc42"
  },
  "rawId": "8:acs:5f6d34d4-dad2-48b1-bad6-30e9cc12bcc8_0000002c-0e29-50fd-72e4-6f8ded7cbc42"
}
```

2. アクセストークンを発行 (今回は不要)

```bash
# 接続文字列を設定済みなら、 --connection-string 以後は不要
az communication identity token issue --scope voip --connection-string "yourConnectionString"
```

応答は以下のよう。

```json
{
  "expires_on": "2025-12-31T08:02:49.1509468+00:00",
  "token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjAxOUQzMTYyMzQ0RTQ4REEwNUU1OUQxMzYwNkYwQkFDRjU4QTQwRUMiLCJ4NXQiOiJBWjB4WWpST1NOb0Y1WjBUWUc4THJQV0tRT3ciLCJ0eXAiOiJKV1QifQ.eyJza3lwZWlkIjoiYWNzOjVmNmQzNGQ0LWRhZDItNDhiMS1iYWQ2LTMwZTljYzEyYmNjOF8wMDAwMDAyYy0wZTI5LTljYjYtMmRlNi02ZjhkZWQ3YzFjNTciLCJzY3AiOjE3OTIsImNzaSI6IjE3NjcwODE3NjkiLCJleHAiOjE3NjcxNjgxNjksInJnbiI6ImFtZXIiLCJhY3NTY29wZSI6InZvaXAiLCJyZXNvdXJjZUlkIjoiNWY2ZDM0ZDQtZGFkMi00OGIxLWJhZDYtMzBlOWNjMTJiY2M4IiwicmVzb3VyY2VMb2NhdGlvbiI6InVuaXRlZHN0YXRlcyIsImlhdCI6MTc2NzA4MTc2OX0.YZwxA-SwlOqOFUxkgbwpYB6zfRSumcNFxYa_D1nu7lZzVqcajq4A6UyLKTW2fUUusfmdd0ugDCwrjMjaWs7d9HSzygIlmuU_pR5FTlG34_phD36pIm7sLC0D-P3rRGIbzuz69w_2u6-zk_fjaqaALXVpo9eT8za0pcRCC5hhM9FMiOncxHDddQHbs50jp85GJKKUv-Ti2PW3DW1cxJtVf88MEuMw7SsbIrjO5f1umiAyWiNoQPc7iG7ocHlx8W33b6vZ77i40f-i9sVx3KB143sZhSeOuKkrOLr83Ol51Y3IBc41jHgkl3NjcBqcQ6pPWBv8b-j2e9TCGm_BHlJKbQ",
  "user_id": "8:acs:5f6d34d4-dad2-48b1-bad6-30e9cc12bcc8_0000002c-0e29-9cb6-2de6-6f8ded7c1c57"
}
```

## 3-a （おすすめ）ローカル検証: Server が先に発信 → ブラウザで着信 Accept

Event Grid の IncomingCall ルーティングが用意できない/面倒な場合は、Web UI から自分の `userId` を作って、サーバーに「自分へ発信」させる方式で検証できます。
この方式だと、Bot ID の事前準備や `/api/incomingCall` への Event Grid ルーティング無しで、ACS + Media Streaming の経路を確認できます（ただし **公開URLは必須**です）。

### 1. サーバーを起動（`CALLBACK_URI_HOST` は上記の通り公開URLにする）

### 2. `startup_server.sh` を実行

```bash
./startup_server.sh
```

### 3. `startup_web.sh` を実行

```bash
./startup_web.sh
```

### 4. UI を開く

- ブラウザで http://localhost:5173 を開く

![alt text](image1.png)

### 5. User IDの指定

1. 【Target Identity】に、相手もしくはBotのIDを指定し、【Start Call】を選択する。

![alt text](image6.png)

2. 右上の【Init (Token)】をクリックする。これにより、ユーザーIDが自動生成される。右上の【agent ready】を確認する。

  ![alt text](image3.png)

### 6. 【Server Start】をクリックしてACSと接続、通話を開始する。

1. 【Server Start】をクリックする。これにより、右上の【server started call】と右下の【Incoming: ringing】を確認する。

![alt text](image4.png)

> ⚠ 【Incoming: ringing】に遷移しない場合は、再度【Server Start】をクリックすると遷移します。

2. 【Accept】をクリックしてACSと接続、通話を開始する。

![alt text](image5.png)

### 7. 実際に発話する。

- 最初はブラウザからマイク利用の許可を尋ねてきますので、許可してください。これでAI音声が返ってくるはず。

## 3-b 指定したユーザーへ発信

- サーバーや別の相手に対して通話する場合です（現在動作しません）。

### Teams interop（Entra ID object ID 固定）で発信する

`POST /api/call/start` は Teams 宛発信に対応しています。

- `targetKind` は固定で `"teams"`
- `entraObjectId` は Teams ユーザーの Entra ID object ID

例:

```bash
curl -sS -X POST http://localhost:8000/api/call/start \
  -H 'content-type: application/json' \
  -d '{
    "targetKind": "teams",
    "entraObjectId": "00000000-0000-0000-0000-000000000000",
    "sourceDisplayName": "Realtime Server"
  }'
```

補足:

- Teams interop は Azure 側の設定（ACS リソースの Teams interop 有効化、テナント前提など）が必要です。
- 既存の ACS User ID 発信（`targetKind:"acs"` + `targetUserId`）も従来通り利用できます。

### 1. サーバーを起動（`CALLBACK_URI_HOST` は上記の通り公開URLにする）

### 2. `startup_server.sh` を実行

```bash
./startup_server.sh
```

### 3. `startup_web.sh` を実行

```bash
./startup_web.sh
```

### 4. UI を開く

- ブラウザで http://localhost:5173 を開く

![alt text](image1.png)

### 5. サーバーBot ID/ACS User IDを使う


3. 実際に発話してください。最初はブラウザからマイク利用の許可を尋ねてきますので、許可してください。

これでAI音声が返ってくるはず。
  Target Identity に通話先の ACS Identity (User ID または Bot ID) を入力
   - サーバー側で ACS Call Automation が待ち受けている Identity を指定します。
   - 事前に ACS リソース側で Event Grid 等を使用し、Incoming Call イベントがサーバーの `/api/incomingCall` にルーティングされるよう設定が必要です。

3. [Start Call] を押し、通話が開始されることを確認
  - 右上に [connected]
  - [Start Call] のすぐ下に [Call: Connected]


## 4. Web UI を Docker イメージで起動 (non-root)

`web/` は Vite でビルドした静的ファイルを Nginx で配信。

```bash
cd web
docker build -t aoai-realtime-web:nonroot .
docker run --rm -p 8080:8080 aoai-realtime-web:nonroot
```

- ブラウザ: `http://localhost:8080/`
- Target Identity に通話先の ACS Identity を入力

## 4. Server を Docker イメージで起動（non-root）

`server/` の Dockerfile は non-root ユーザーで FastAPI(Uvicorn) を起動する

```bash
cd server
docker build -t aoai-realtime-server:nonroot .

# 必要な環境変数を渡して起動（値は自分の環境に合わせて設定）
docker run --rm -p 8000:8000 \
  -e AZURE_COMMUNICATION_CONNECTION_STRING=... \
  -e AZURE_OPENAI_ENDPOINT=... \
  -e AZURE_OPENAI_DEPLOYMENT=... \
  -e AZURE_OPENAI_API_KEY=... \
  -e AOAI_VOICE=... \
  -e CALLBACK_URI_HOST=... \
  aoai-realtime-server:nonroot
```

## 5. ACAで動作

- 各Dockerイメージを各ACAで利用
  - ingress、listening portを間違えないように

    | Server/Client | Port number/protocol  |
    |--------------|-------------:|
    | UI (node.js) | 8080/tcp    |
    | server (Python) | 8000/tcp    |

- UI (JavaScript)、server (Python) とも、パブリックアクセスを許可、もしくはVNET内からのアクセスを許可する
  - WebSocket over TLS (wss://) のため、はACAアプリ名のみを使ったアクセスはできない
  - VNET内に閉じたアクセスの場合、Private DNS zoneを構成する必要がある

### ACA / Ingress チェックリスト（落とし穴回避）

- `CALLBACK_URI_HOST` は **https の公開 URL**（例: `https://<app>.<region>.azurecontainerapps.io`）にする
- ACS が参照するのは
  - `POST /api/incomingCall`（Event Grid）
  - `POST /api/callbacks`（Call Automation callback）
  - `wss://.../ws/media`（Media Streaming）
  の 3 つなので、Ingress / ルーティングがすべて到達できることを確認する
- WebSocket を使うため、TLS 終端後に WebSocket upgrade が通る構成にする（HTTP が開けても WS が塞がれているケースがあります）
- 統合ゲートウェイ方式（推奨）を使う場合は **公開ポートは 8000 のみ**で OK（HTTP と `/ws/media` を同一アプリで処理）