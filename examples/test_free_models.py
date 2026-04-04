import os
import sys

# 將 omni_router 加入模組搜尋路徑 (從 examples 回退一層)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from omni_router.core import AIProvider, AIRouter

# 建立實作 AIProvider 的測試合集類別
class MockProvider(AIProvider):
    def __init__(self, name, api_key, base_url):
        super().__init__(name=name, api_key=api_key, base_url=base_url)

if __name__ == "__main__":
    # 配置多個 Provider：包含 OpenRouter 和 Groq
    openrouter_provider = MockProvider(
        name="my_openrouter", 
        api_key="dummy_key", 
        base_url="https://openrouter.ai/api/v1"
    )
    
    # 建立 Router
    router = AIRouter(
        providers={
            "openrouter": openrouter_provider,
        }, 
        model_routing={}
    )
    
    print("正在向所有已登錄的 Provider 抓取免費模型列表...")
    free_models_map = router.get_all_free_models()
    
    print("\n--- 抓取結果 ---")
    for provider_name, models in free_models_map.items():
        print(f"✅ Provider: [{provider_name}]")
        print(f"   總共找到 {len(models)} 個免費模型。")
        print("   前 10 個模型：")
        for m in models[:10]:
            print(f"     - {m}")
        print("-" * 20)
