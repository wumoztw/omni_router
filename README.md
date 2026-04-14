# 🔌 OmniRouter (全能 AI 路由器)

`omni_router` 是一個輕量級的中間層架構 (Middleware)，專為解決當前軟體開發中依賴單一 AI 廠商 API 所帶來的不穩定性（如網路斷線、額度耗盡、官方當機等問題）而誕生。透過建立強健的多模型備援機制，確保您的系統永遠在線。

## ✨ 7 大核心功能與特色

### 1. 統一的無縫接軌介面 (Universal Wrapper)
由於當今多數 AI 平台皆支援與 OpenAI 相容的終端格式 (`base_url`)，OmniRouter 底層全面統一使用 `openai` 的 Python SDK 進行連線包裝。無論您需要對接 1 家還是 10 家不同的 AI 提供商，所有進送的 Prompt 與歷史記錄格式永遠只需編寫一次，大幅簡化跨平台開發難度。

### 2. 智能多層瀑布式備援 (Cascading Failover)
這套系統如同一個極其負責的「接線生」：
- **網路斷線抗性**：內建「指數退避演算法 (Exponential Backoff)」，遇到 `APIConnectionError` 時會暫停並自動重試，過濾短時間的網路波動。
- **配額與限頻捕獲**：一旦捕獲 `429 Too Many Requests`，或者錯誤訊息中含有 `quota`、`limit` 等關鍵字，系統**絕對不會崩潰退出**。它會優雅地攔截例外、印出警告日誌，並在一瞬間將相同請求轉發給順位接替的下一個 AI 模型（例如從 Groq 切換至 Google Gemini），直到有人成功回答為止。

### 3. 解耦的插拔式設計 (Decoupled Design)
這是一個不與特定專案環境綁死的「空殼引擎」，對外提供了豐富的回呼（Hooks）介面：
- **`cost_callback=...` (記帳回呼)**：每次請求完畢，提供商名稱、成功與否、傳入與吐出的 Token 數量將精準送回，讓您的專案（如 CyberNewsPlurk）可以完整自行記帳。
- **`sanitize_func=...` (過濾掛載)**：在將資料送給 AI 前，攔截並拔除有毒的 Prompt 或干擾訊息。
- **`get_rate_limiter()`**：提供限頻物件掛載介面，系統可在本地端判定超過額度而自動轉移，徹底節省無效網路連線等待。

### 4. 懶加載與資源優化 (Lazy Initialization)
即使您註冊了 10 家不同 AI 提供商的 API 金鑰，此套件也絕不會在系統一啟動時就瘋狂建立所有的網路連線。它使用了懶加載機制，只有演算法判定「現在輪到呼叫該供應商」時，才會實例化並進行握手，大幅減省未動用之記憶體與等待時間。

### 5. 相容的回傳模式 (Return Metadata Mode)
- 若您只需要普通的內容回復：直接回傳生成的純字串。
- 若您的專案需要留下完整的稽核與日誌（如 TitanCore）：只要啟用 `return_metadata=True` 旗標，系統便會優雅地回傳包含執行結果狀態、使用的具體供應商與模型的 JSON 字典。

### 6. 動態模型自動探索 (Dynamic Model Discovery)
系統具備自動抓取最新可用模型的能力，無需手動頻繁更新代碼中的模型字串列：
- **OpenRouter 深度發現**：自動呼叫 OpenRouter API 獲取所有標價為 0 的免費模型名單。
- **Google Gemini 支援**：支援自動探索 Google AI Studio 可用模型。
- **本地端/Groq 自動對接**：自動列出本地端 (Ollama/LM Studio) 或 Groq 平台當前可供調用的模型。

### 7. 多帳號金鑰自動輪詢 (Multi-account Key Rotation)
針對免費額度有限的平台（如 Groq、Gemini），支援同時傳入多組 API 金鑰：
- **無縫接力**：當帳號 A 額度耗盡 (429/Quota) 時，Provider 內部會自動切換至帳號 B 並重試，對業務代碼完全透明。
- **失效記憶**：系統會記錄在本 Session 中已失效的金鑰，避免重複無效嘗試。
- **配置靈活**：支援傳入 `list` 或以逗號分隔的環境變數。

## 🚀 快速安裝
您可以直接在任何 Python 專案的 `requirements.txt` 中添加以下程式碼：

```text
git+https://github.com/wumoztw/omni_router.git
```

或是使用命令列安裝：
```bash
pip install git+https://github.com/wumoztw/omni_router.git
```

## 🛠️ 基本使用範例

```python
from omni_router import AIRouter, AIProvider
import os

# 1. 建立具有對應金鑰的供應商（需要另外繼承覆寫）
class GroqProvider(AIProvider):
    def __init__(self, key):
        super().__init__("groq", key, "https://api.groq.com/openai/v1")

class GoogleProvider(AIProvider):
    def __init__(self, key):
        super().__init__("google", key, "https://generativelanguage.googleapis.com/v1beta/openai/")

providers = {
    # 支援傳入多組金鑰（List 或 逗號分隔字串），當額度耗盡時會自動接力
    "groq": GroqProvider(os.getenv("GROQ_API_KEY")), # 例如: "key1,key2,key3"
    "google": GoogleProvider(os.getenv("GOOGLE_AI_API_KEY"))
}

# 2. 定義瀑布流備援順序
model_routing = {
    "default": [
        {"model": "llama-3.3-70b-versatile", "provider": "groq"},
        {"model": "gemma-4-26b", "provider": "google"}
    ]
}

# 3. 初始化路由器
router = AIRouter(providers=providers, model_routing=model_routing)

# 4. 啟動無縫查詢
result = router.chat_complete(
    system="你是一位專業的助手",
    user="請用一句話解釋量子力學。",
    routing_key="default"
)

print(result)
```

有了 OmniRouter，未來遇到更好、更便宜的新 AI 廠商時，只需在列表多加一行設定，整個生態系就能瞬間補強，省去重寫邏輯的無盡噩夢。
