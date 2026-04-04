import os
import sys
import streamlit as st

# ======== 環境設定與引入 ========
# 將 omni_router 動態加入模組路徑，以便讀取核心模組 (從 examples 回退一層到 repository root)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from omni_router.core import AIProvider, AIRouter
except ImportError:
    st.error("找不到 omni_router 模組，請確定您在正確的目錄執行此腳本。")
    st.stop()

# ======== 頁面配置 ========
st.set_page_config(page_title="OmniRouter Chat", page_icon="🌌", layout="centered")
st.title("🌌 OmniRouter (免費模型代理測試站)")

# ======== 快取/初始化 Router ========
# 我們使用 Streamlit 的快取機制，避免每次畫面重新渲染都去抓模型
@st.cache_resource
def init_router():
    class DynamicProvider(AIProvider):
        def __init__(self, name, api_key, base_url):
            super().__init__(name=name, api_key=api_key, base_url=base_url)

    # 讀取環境變數 (在啟動 streamlit 時請確保傳入)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "dummy_key")
    groq_key = os.environ.get("GROQ_API_KEY", "")

    providers = {
        "openrouter": DynamicProvider("openrouter", openrouter_key, "https://openrouter.ai/api/v1"),
        "groq": DynamicProvider("groq", groq_key, "https://api.groq.com/openai/v1"),
    }
    # 過濾掉不可用且非 openrouter 的 (openrouter API抓取模型時可以不用正確 key)
    providers = {k: v for k, v in providers.items() if v.is_available() or v.name == "openrouter"}
    
    return AIRouter(providers=providers, model_routing={})

@st.cache_data
def get_free_models(_router):
    return _router.get_all_free_models()

router = init_router()

# 初始化對話歷史的狀態
if "messages" not in st.session_state:
    st.session_state.messages = []

# ======== 側邊欄 ========
with st.sidebar:
    st.header("⚙️ 路由設定")
    with st.spinner("正在掃描免費模型節點..."):
        free_models_map = get_free_models(router)
    
    if not free_models_map:
        st.warning("找不到任何免費模型，請確認網路連線或 API Key 是否提供正確。")
        provider_options = []
    else:
        st.success("模型庫載入完畢！")
        provider_options = list(free_models_map.keys())
    
    selected_provider = st.selectbox("1. 選擇供應商", options=provider_options)
    
    selected_model = None
    if selected_provider:
        models = free_models_map[selected_provider]
        selected_model = st.selectbox("2. 選擇模型", options=models)

    st.divider()
    st.subheader("🌐 即時資訊 (RAG)")
    enable_web_search = st.checkbox("啟用 Tavily 網路搜尋", value=False)
    tavily_api_key = os.environ.get("TAVILY_API_KEY", "")
    if enable_web_search and not tavily_api_key:
        st.warning("⚠️ 啟用搜尋需要提供環境變數 TAVILY_API_KEY。")

    if st.button("🗑️ 清空對話"):
        st.session_state.messages = []
        st.rerun()

# ======== 動態註冊路由 ========
target_route = "ui_selected"
if selected_provider and selected_model:
    router.model_routing[target_route] = [
        {"provider": selected_provider, "model": selected_model}
    ]

# ======== 主對話區 ========
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 接收使用者輸入
if prompt := st.chat_input("輸入訊息以發送給選定的模型..."):
    if not selected_model:
        st.error("請先從側邊欄選定一個供應商與模型！")
    else:
        # 顯示並儲存使用者訊息
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 呼叫 OmniRouter
        with st.chat_message("assistant"):
            with st.spinner(f"正在透過 {selected_provider} 請求 {selected_model}..."):
                try:
                    response_text = router.chat_complete(
                        system="You are a helpful AI.",
                        user=prompt,
                        routing_key=target_route,
                        enable_web_search=enable_web_search,
                        tavily_api_key=tavily_api_key
                    )
                    st.markdown(response_text)
                    st.session_state.messages.append({"role": "assistant", "content": response_text})
                except Exception as e:
                    st.error(f"路由失敗: {str(e)}\n\n(提示: 部分 API 如 OpenRouter 就算抓到清單，聊天時仍需要真正的 Key)")
