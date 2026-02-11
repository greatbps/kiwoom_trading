"""
사용 가능한 Gemini 모델 확인
"""
import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ GEMINI_API_KEY가 설정되지 않았습니다.")
    exit(1)

print(f"API Key: {api_key[:20]}...")
print("\n사용 가능한 Gemini 모델 목록:\n")

try:
    genai.configure(api_key=api_key)

    for model in genai.list_models():
        if 'generateContent' in model.supported_generation_methods:
            print(f"✓ {model.name}")
            print(f"  - Display Name: {model.display_name}")
            print(f"  - Description: {model.description}")
            print()
except Exception as e:
    print(f"❌ 오류: {e}")
