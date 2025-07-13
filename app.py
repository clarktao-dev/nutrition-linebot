import os
import base64
import openai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, TextSendMessage
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# 初始化
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai.api_key = OPENAI_API_KEY

# 營養師角色設定
NUTRITION_PROMPT = """
你是一位擁有20年經驗的專業營養師，請分析用戶上傳的食物照片並提供：
1. 食物識別與熱量估算
2. 營養成分分析（蛋白質、碳水化合物、脂肪、維生素）
3. 健康建議
4. 改善建議

請用親切、專業的語調回應，就像家庭營養師一樣。回應請用繁體中文。
"""

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_message = event.message.text
    
    if user_message in ["開始", "hi", "hello", "你好", "Hello"]:
        welcome_text = """👋 歡迎使用AI營養師！

我是你的專屬營養師，擁有20年專業經驗，可以幫你：

📸 分析食物照片
💡 提供營養建議  
📊 估算熱量
🥗 給出健康建議

請直接上傳食物照片，我會立即為你分析營養成分！"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=welcome_text)
        )
    else:
        help_text = """請上傳食物照片，我會為你分析：

📸 上傳方式：
1. 點擊聊天室下方的相機圖示
2. 拍攝或選擇食物照片
3. 發送給我

我會立即分析食物的營養成分並給你專業建議！"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=help_text)
        )

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    try:
        # 發送處理中訊息
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🔍 正在分析你的食物照片，請稍候...")
        )
        
        # 下載圖片
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b''.join(message_content.iter_content())
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # 使用 OpenAI 分析圖片
        response = openai.ChatCompletion.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "system",
                    "content": NUTRITION_PROMPT
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "請詳細分析這張食物照片的營養成分，並提供專業的營養師建議。"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000
        )
        
        analysis_result = response.choices[0].message.content
        
        # 發送分析結果
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=f"🍽️ 營養分析結果：\n\n{analysis_result}")
        )
        
    except Exception as e:
        error_message = f"抱歉，圖片分析出現問題：{str(e)}\n\n請稍後再試，或確認：\n1. 圖片清晰可見\n2. 包含完整食物\n3. 光線充足"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)