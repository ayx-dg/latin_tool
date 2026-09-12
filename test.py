import google.generativeai as genai
from django.conf import settings

genai.configure(api_key='AIzaSyDFIiLy6mluyyGO8_xb5t_BQmci9dHONkk')

print("--- 可用模型列表 ---")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)
