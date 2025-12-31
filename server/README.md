
# Server (ACS Call Automation + Media Streaming + AOAI Realtime)

このディレクトリは以下を提供します。

- FastAPI サーバー: IncomingCall(Event Grid) を受けて `answer_call`、Media Streaming を開始
- Media WS ブリッジ: ACS の音声ストリームを AOAI Realtime に中継し、必要に応じて応答音声を返送
- （任意）Foundry Agent: 転記テキストを Foundry Agent（Web grounding）に渡し、結果を音声化

## 必須環境変数

- `AZURE_COMMUNICATION_CONNECTION_STRING`
- `CALLBACK_URI_HOST`（例: `https://<public-host>`）
- AOAI Realtime
	- `AZURE_OPENAI_ENDPOINT`
	- `AZURE_OPENAI_API_KEY`
	- `AZURE_OPENAI_DEPLOYMENT`
	- `AZURE_OPENAI_API_VERSION`
	- `AOAI_VOICE`

## Event Grid SubscriptionValidation

Event Grid のサブスクリプション作成時に `SubscriptionValidationEvent` が送られます。
本サーバーは `POST /api/incomingCall` で `validationResponse` を返して検証に応答します。

## （任意）Foundry Agent（Web grounding）

Foundry 側で Web grounding を有効にした Agent を用意し、以下を設定します。

- `AZURE_AI_PROJECT_ENDPOINT`（または `AZURE_FOUNDRY_PROJECT_ENDPOINT`）
- `AZURE_AI_AGENT_ID`（または `AZURE_FOUNDRY_AGENT_ID`）

Agent 実行制御（任意）:

- `MEDIA_WS_AGENT_ENABLE`（`0/1`）
- `MEDIA_WS_AGENT_TIMEOUT_MS`（既定 `2000`）
- `MEDIA_WS_AGENT_FALLBACK_PREFIX`（既定 `今は検索できないので一般知識で答えます`）

フォールバック挙動:

- Agent の呼び出しが失敗/タイムアウト/空結果のときのみ、上記プレフィックスを付けて一般知識回答に切り替えます。

## Realtime 再接続（通話維持）

AOAI Realtime 側の WebSocket が落ちた場合でも、ACS Media WS を切断せずに再接続を試みます。
復旧後に応答を継続できるよう、指数バックオフで再接続します。

