# 環境の作り方

以下はLinuxで動作確認済みです。Windowsでも同様ですが、環境変数の設定構文の違いは適宜環境に合わせてください。

## 1. 環境変数

サーバー側の環境変数は `server/.env` にまとめて管理するのを推奨します（秘密情報をシェルスクリプトに直書きしない）。

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

# （任意）音質改善: リサンプリング品質 (soxr)
export MEDIA_WS_SOXR_QUALITY=VHQ
```

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
python server/scripts/ws_probe.py --url https://<public-host> --path /ws/media
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